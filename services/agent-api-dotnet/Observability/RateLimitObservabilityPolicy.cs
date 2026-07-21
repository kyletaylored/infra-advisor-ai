using System.ClientModel.Primitives;
using System.Diagnostics.Metrics;

namespace InfraAdvisor.AgentApi.Observability;

// System.ClientModel pipeline policy (the OpenAI/Azure.AI.OpenAI SDK's HTTP
// pipeline abstraction — not Azure.Core's) that observes 429 responses from
// Azure OpenAI on every retry attempt. The SDK's built-in ClientRetryPolicy
// retries 429s transparently before AgentService ever sees the call, so
// without this hook a sustained rate-limit backoff is invisible — it just
// shows up as an unexplained 30-60s "chat" span with a generic error buried
// in a child span (see agent-api-dotnet remediation plan, Fix 3). Registered
// at PipelinePosition.PerTry so it runs on every retry, not just once.
public sealed class RateLimitObservabilityPolicy : PipelinePolicy
{
    private readonly Counter<long> _counter;
    private readonly ILogger _logger;

    public RateLimitObservabilityPolicy(Counter<long> counter, ILogger logger)
    {
        _counter = counter;
        _logger = logger;
    }

    public override void Process(PipelineMessage message, IReadOnlyList<PipelinePolicy> pipeline, int currentIndex)
    {
        ProcessNext(message, pipeline, currentIndex);
        Observe(message);
    }

    public override async ValueTask ProcessAsync(
        PipelineMessage message, IReadOnlyList<PipelinePolicy> pipeline, int currentIndex)
    {
        await ProcessNextAsync(message, pipeline, currentIndex).ConfigureAwait(false);
        Observe(message);
    }

    private void Observe(PipelineMessage message)
    {
        if (message.Response?.Status == 429)
        {
            _counter.Add(1, new KeyValuePair<string, object?>(
                "endpoint", message.Request.Uri?.Host ?? "unknown"));
            _logger.LogWarning(
                "Azure OpenAI rate-limited (429) on {Host}{Path} — SDK retry policy will back off and retry.",
                message.Request.Uri?.Host, message.Request.Uri?.AbsolutePath);
        }
    }
}
