using Microsoft.AspNetCore.Builder;
using OpenTelemetry;
using OpenTelemetry.Exporter;
using OpenTelemetry.Resources;
using OpenTelemetry.Metrics;
using OpenTelemetry.Trace;

namespace InfraAdvisor.AgentApi.Observability;

public static class TelemetrySetup
{
    // Shared ActivitySource name for all spans in this service (agent, router, specialist, llm).
    // Both the DD bridge global provider (DD_TRACE_OTEL_ENABLED=true → APM) and the
    // non-global OTLP LLMObs provider register this source, so all spans reach both sinks.
    public const string ActivitySourceName = "infra-advisor-agent-api-dotnet";

    public static void Configure(WebApplicationBuilder builder)
    {
        var otlpEndpoint = Environment.GetEnvironmentVariable("OTEL_EXPORTER_OTLP_ENDPOINT")
            ?? "http://datadog-agent.datadog.svc.cluster.local:4318";
        var serviceName = Environment.GetEnvironmentVariable("OTEL_SERVICE_NAME")
            ?? "infra-advisor-agent-api-dotnet";
        var ddEnv = Environment.GetEnvironmentVariable("DD_ENV") ?? "dev";
        var ddVersion = Environment.GetEnvironmentVariable("DD_VERSION") ?? "latest";

        // Metrics: OTLP HTTP/protobuf to the Datadog agent collector.
        builder.Services.AddOpenTelemetry()
            .ConfigureResource(r => r
                .AddService(serviceName)
                .AddAttributes(new Dictionary<string, object> {
                    ["deployment.environment"] = ddEnv,
                    ["service.version"] = ddVersion,
                })
            )
            .WithMetrics(metrics => metrics
                .AddAspNetCoreInstrumentation()
                .AddHttpClientInstrumentation()
                .AddMeter(ActivitySourceName)
                .AddOtlpExporter(otlp => {
                    otlp.Endpoint = new Uri($"{otlpEndpoint.TrimEnd('/')}/v1/metrics");
                    otlp.Protocol = OtlpExportProtocol.HttpProtobuf;
                })
            );

        // LLMObs: second non-global TracerProvider sends gen_ai.* spans directly to
        // Datadog LLM Observability via OTLP. The dd-otlp-source=llmobs header routes
        // these spans into the LLMObs product instead of APM.
        // The DD bridge (global provider, DD_TRACE_OTEL_ENABLED=true) handles APM separately.
        var ddApiKey = Environment.GetEnvironmentVariable("DD_API_KEY") ?? "";
        var ddSite = Environment.GetEnvironmentVariable("DD_SITE") ?? "datadoghq.com";
        var llmObsEndpoint = Environment.GetEnvironmentVariable("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
            ?? $"https://otlp.{ddSite}";

        if (!string.IsNullOrEmpty(ddApiKey))
        {
            var llmObsProvider = Sdk.CreateTracerProviderBuilder()
                .AddSource(ActivitySourceName)
                // AlwaysOnSampler ignores the parent's sampling flag.
                // Without this the default ParentBasedSampler inherits the DD bridge's
                // HTTP span sampling decision, which can be Recorded=0 for UI requests,
                // making SetTag() a no-op and producing attribute-less spans that
                // DD LLMObs silently drops. Kafka traces were immune because their
                // root span (eval.agent_run) has no parent → AlwaysOn fires automatically.
                .SetSampler(new AlwaysOnSampler())
                .ConfigureResource(r => r
                    .AddService(serviceName)
                    .AddAttributes(new Dictionary<string, object> {
                        ["deployment.environment"] = ddEnv,
                        ["service.version"] = ddVersion,
                    })
                )
                .AddOtlpExporter(otlp => {
                    otlp.Endpoint = new Uri($"{llmObsEndpoint.TrimEnd('/')}/v1/traces");
                    otlp.Headers = $"dd-api-key={ddApiKey},dd-otlp-source=llmobs";
                    otlp.Protocol = OtlpExportProtocol.HttpProtobuf;
                })
                .Build();
            builder.Services.AddSingleton(llmObsProvider);
        }

        // DD_LOGS_INJECTION=true causes the DD SDK to inject dd.trace_id/dd.span_id into
        // ILogger structured properties so the agent can correlate stdout logs with APM traces.
        builder.Logging.ClearProviders();
        builder.Logging.AddConsole(opts => opts.FormatterName = "simple");
    }
}
