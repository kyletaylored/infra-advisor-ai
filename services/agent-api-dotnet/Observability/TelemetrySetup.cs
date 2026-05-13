using Microsoft.AspNetCore.Builder;
using OpenTelemetry;
using OpenTelemetry.Exporter;
using OpenTelemetry.Resources;
using OpenTelemetry.Metrics;
using OpenTelemetry.Trace;

namespace InfraAdvisor.AgentApi.Observability;

// OpenTelemetry setup for the agent-api-dotnet service.
//
// Trace sources captured:
//   - "Experimental.Microsoft.Extensions.AI" → chat + execute_tool spans
//     emitted by Microsoft.Extensions.AI's .UseOpenTelemetry() decorator
//     on the IChatClient pipeline.
//   - ActivitySourceName below ("infra-advisor-agent-api-dotnet") →
//     the invoke_agent span emitted by Microsoft.Agents.AI's
//     .UseOpenTelemetry(sourceName:) decorator on the agent builder.
//   - AspNetCore + HttpClient → HTTP server / client spans (root of trace +
//     the outbound POST to Azure OpenAI inside the chat span).
//
// Vendor routing (Datadog-specific dd-otlp-source=llmobs header,
// ml_app attribute injection, etc.) lives in the in-cluster collector's
// otelCollector config, not here. The app speaks pure OTLP.
public static class TelemetrySetup
{
    // The ActivitySource we pass to Microsoft.Agents.AI's
    // .UseOpenTelemetry(sourceName: ...) call. Same value gets AddSource'd
    // on the TracerProvider so the agent's invoke_agent spans are exported.
    public const string ActivitySourceName = "infra-advisor-agent-api-dotnet";

    public static void Configure(WebApplicationBuilder builder)
    {
        var otlpEndpoint = Environment.GetEnvironmentVariable("OTEL_EXPORTER_OTLP_ENDPOINT")
            ?? "http://datadog-agent.datadog.svc.cluster.local:4318";
        var serviceName = Environment.GetEnvironmentVariable("OTEL_SERVICE_NAME")
            ?? "infra-advisor-agent-api-dotnet";
        var ddEnv = Environment.GetEnvironmentVariable("DD_ENV") ?? "dev";
        var ddVersion = Environment.GetEnvironmentVariable("DD_VERSION") ?? "latest";

        builder.Services.AddOpenTelemetry()
            .ConfigureResource(r => r
                .AddService(serviceName)
                .AddAttributes(new Dictionary<string, object>
                {
                    ["deployment.environment"] = ddEnv,
                    ["service.version"]        = ddVersion,
                }))
            .WithTracing(t => t
                // Library auto-instrumentations: HTTP server span (trace root),
                // outbound HTTP client spans (Azure OpenAI REST POST + MCP HTTP).
                .AddAspNetCoreInstrumentation()
                .AddHttpClientInstrumentation()

                // GenAI span sources.
                .AddSource("Experimental.Microsoft.Extensions.AI")
                .AddSource(ActivitySourceName)

                // AlwaysOn — keep the agent loop's spans regardless of the
                // DD bridge's sampling decision on the HTTP parent.
                .SetSampler(new AlwaysOnSampler())

                .AddOtlpExporter(otlp =>
                {
                    otlp.Endpoint = new Uri($"{otlpEndpoint.TrimEnd('/')}/v1/traces");
                    otlp.Protocol = OtlpExportProtocol.HttpProtobuf;
                }))
            .WithMetrics(metrics => metrics
                .AddAspNetCoreInstrumentation()
                .AddHttpClientInstrumentation()
                .AddMeter(ActivitySourceName)
                .AddOtlpExporter(otlp =>
                {
                    otlp.Endpoint = new Uri($"{otlpEndpoint.TrimEnd('/')}/v1/metrics");
                    otlp.Protocol = OtlpExportProtocol.HttpProtobuf;
                }));

        // DD_LOGS_INJECTION=true causes the DD SDK to inject
        // dd.trace_id/dd.span_id into ILogger structured properties for
        // log-trace correlation.
        builder.Logging.ClearProviders();
        builder.Logging.AddConsole(opts => opts.FormatterName = "simple");
    }
}
