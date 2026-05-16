using Microsoft.AspNetCore.Builder;
using Microsoft.Extensions.Hosting;
using Npgsql;
using OpenTelemetry;
using OpenTelemetry.Exporter;
using OpenTelemetry.Resources;
using OpenTelemetry.Metrics;
using OpenTelemetry.Trace;
using Serilog;
using Serilog.Formatting.Compact;

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
                    // Identifies the span emitter as OpenTelemetry (not the
                    // dd-trace SDK) so DD LLMObs / APM tags spans source:otel
                    // instead of source:undefined. Same value DD's external-
                    // evaluations API expects in the `tags` array when scoring
                    // these spans (docs/dd-otel.md).
                    ["source"]                 = "otel",
                }))
            .WithTracing(t => t
                // Library auto-instrumentations: HTTP server span (trace root),
                // outbound HTTP client spans (Azure OpenAI REST POST + MCP HTTP),
                // Npgsql command spans (every SQL query — gives DBM something
                // to anchor SQL-comment trace propagation against when the
                // DD .NET tracer's auto-instrumentation runs alongside).
                .AddAspNetCoreInstrumentation()
                .AddHttpClientInstrumentation()
                .AddNpgsql()

                // GenAI span sources.
                .AddSource("Experimental.Microsoft.Extensions.AI")
                // MCP client-side spans (outbound tool calls into
                // mcp-server-dotnet). Same source name the server emits on,
                // so the two services share one trace tree end-to-end.
                .AddSource("Experimental.ModelContextProtocol")
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

        // Structured JSON logs via Serilog. DatadogTraceContextEnricher
        // copies the ambient OTel Activity's trace/span IDs into each log
        // event as dd.trace_id / dd.span_id — required for DD APM trace
        // → Logs correlation now that we no longer run the DD .NET tracer
        // (which used to handle this via DD_LOGS_INJECTION=true).
        // RenderedCompactJsonFormatter emits one JSON object per line for
        // the DD Agent's csharp log source.
        builder.Host.UseSerilog((ctx, services, lc) => lc
            .Enrich.FromLogContext()
            .Enrich.With(new DatadogTraceContextEnricher())
            .Enrich.WithProperty("service.name", "infra-advisor-agent-api-dotnet")
            .WriteTo.Console(new RenderedCompactJsonFormatter()));

        // One-line startup confirmation. Grep pod logs for "[otel]" if traces
        // aren't appearing in DD — confirms the pipeline initialized with the
        // expected source list.
        Console.WriteLine(
            "[otel] tracing sources: AspNetCore, Http, Npgsql, " +
            "Experimental.Microsoft.Extensions.AI, " +
            "Experimental.ModelContextProtocol, " + ActivitySourceName);
    }
}
