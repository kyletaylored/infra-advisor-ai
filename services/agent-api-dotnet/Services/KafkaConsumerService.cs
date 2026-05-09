using System.Diagnostics;
using System.Text.Json;
using Confluent.Kafka;
using InfraAdvisor.AgentApi.Models;
using InfraAdvisor.AgentApi.Observability;

namespace InfraAdvisor.AgentApi.Services;

public class KafkaConsumerService : BackgroundService
{
    private readonly IServiceProvider _serviceProvider;
    private readonly ILogger<KafkaConsumerService> _logger;
    private const string ConsumerTopic = "infra.query.events";
    private const string ProducerTopic = "infra.eval.results";
    private const string GroupId = "infra-advisor-agent-api";

    // Toggle: KAFKA_TRACING_ENABLED=true to emit APM + LLMObs spans for eval-loop
    // messages. Default false because the eval load-generator runs continuously and
    // would otherwise flood both interfaces with eval traces, drowning out real
    // user queries. Read once at startup; flip via configmap + pod restart.
    private static readonly bool KafkaTracingEnabled =
        (Environment.GetEnvironmentVariable("KAFKA_TRACING_ENABLED") ?? "false")
            .Equals("true", StringComparison.OrdinalIgnoreCase);

    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
    };

    public KafkaConsumerService(IServiceProvider serviceProvider, ILogger<KafkaConsumerService> logger)
    {
        _serviceProvider = serviceProvider;
        _logger = logger;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        var bootstrapServers = Environment.GetEnvironmentVariable("KAFKA_BOOTSTRAP_SERVERS")
            ?? "kafka-cluster-kafka-bootstrap.kafka.svc.cluster.local:9092";

        IConsumer<Ignore, string>? consumer = null;
        IProducer<Null, string>? producer = null;

        try
        {
            var consumerConfig = new ConsumerConfig
            {
                BootstrapServers = bootstrapServers,
                GroupId = GroupId,
                AutoOffsetReset = AutoOffsetReset.Latest,
                EnableAutoCommit = true,
            };
            consumer = new ConsumerBuilder<Ignore, string>(consumerConfig).Build();

            var producerConfig = new ProducerConfig
            {
                BootstrapServers = bootstrapServers,
            };
            producer = new ProducerBuilder<Null, string>(producerConfig).Build();

            consumer.Subscribe(ConsumerTopic);
            _logger.LogInformation("Kafka consumer subscribed to {Topic}", ConsumerTopic);
        }
        catch (Exception ex)
        {
            _logger.LogWarning("Kafka consumer failed to start (non-fatal): {Error}", ex.Message);
            consumer?.Dispose();
            producer?.Dispose();
            return;
        }

        try
        {
            while (!stoppingToken.IsCancellationRequested)
            {
                ConsumeResult<Ignore, string>? consumeResult = null;
                try
                {
                    consumeResult = consumer.Consume(stoppingToken);
                }
                catch (OperationCanceledException)
                {
                    break;
                }
                catch (ConsumeException ex)
                {
                    _logger.LogWarning("Kafka consume error: {Error}", ex.Error.Reason);
                    await Task.Delay(1000, stoppingToken);
                    continue;
                }

                if (consumeResult?.Message?.Value == null) continue;

                KafkaQueryEvent? evt;
                try
                {
                    evt = JsonSerializer.Deserialize<KafkaQueryEvent>(consumeResult.Message.Value, JsonOptions);
                }
                catch (Exception ex)
                {
                    _logger.LogWarning("Failed to deserialize Kafka message: {Error}", ex.Message);
                    continue;
                }

                if (evt == null) continue;

                var sw = Stopwatch.StartNew();
                AgentResult? result = null;

                try
                {
                    using var scope = _serviceProvider.CreateScope();
                    var agentService = scope.ServiceProvider.GetRequiredService<AgentService>();

                    // When tracing is disabled, suppress at the AsyncLocal scope level so
                    // every nested ActivitySource.StartActivity (router/specialist/llm/tool)
                    // returns null and nothing reaches APM or LLMObs.
                    using var suppressScope = KafkaTracingEnabled ? null : TracingScope.Suppress();

                    // Eval activity: only created when tracing is enabled. Tags help
                    // distinguish background re-runs from real user queries in DD.
                    using var evalActivity = KafkaTracingEnabled
                        ? LlmTelemetry.ActivitySource.StartActivity("eval.agent_run", ActivityKind.Internal)
                        : null;
                    evalActivity?.SetTag("eval.query_id", evt.QueryId);
                    evalActivity?.SetTag("eval.session_id", evt.SessionId);
                    evalActivity?.SetTag("eval.source", "kafka");

                    result = await agentService.RunAgentAsync(
                        query: evt.Query,
                        sessionId: evt.SessionId,
                        deployment: "",
                        ct: stoppingToken);
                }
                catch (Exception ex)
                {
                    _logger.LogWarning("Agent run failed for query_id={QueryId}: {Error}", evt.QueryId, ex.Message);
                    continue;
                }

                sw.Stop();

                var evalResult = new KafkaEvalResult(
                    SessionId: evt.SessionId,
                    QueryId: evt.QueryId,
                    Query: evt.Query,
                    Answer: result.Answer,
                    Sources: result.Sources,
                    ToolsCalled: result.ToolsCalled,
                    FaithfulnessScore: null,
                    LatencyMs: sw.Elapsed.TotalMilliseconds,
                    CorpusType: evt.CorpusType,
                    Domain: result.QueryDomain
                );

                try
                {
                    var payload = JsonSerializer.Serialize(evalResult, JsonOptions);
                    await producer!.ProduceAsync(ProducerTopic,
                        new Message<Null, string> { Value = payload }, stoppingToken);
                    _logger.LogInformation("Produced eval result for query_id={QueryId}", evt.QueryId);
                }
                catch (Exception ex)
                {
                    _logger.LogWarning("Failed to produce eval result: {Error}", ex.Message);
                }
            }
        }
        finally
        {
            consumer.Close();
            consumer.Dispose();
            producer?.Flush(TimeSpan.FromSeconds(5));
            producer?.Dispose();
        }
    }
}
