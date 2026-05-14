using Microsoft.AspNetCore.Builder;
using OpenTelemetry;
using OpenTelemetry.Exporter;
using OpenTelemetry.Resources;
using OpenTelemetry.Metrics;
using OpenTelemetry.Trace;

namespace InfraAdvisor.McpServer.Observability;

// OpenTelemetry setup for mcp-server-dotnet.
//
// Trace sources captured:
//   - "Experimental.ModelContextProtocol" — MCP server request + tool-
//     execution spans emitted by ModelContextProtocol.AspNetCore 1.3+.
//     These spans link this service to the agent-api-dotnet trace tree
//     via the SDK's traceparent propagation through MCP message metadata.
//   - AspNetCore + HttpClient — server root span + outbound HTTP from
//     tools (FHWA / EPA / EIA / SAM.gov / etc.) so each upstream data
//     source appears as its own span under tool execution.
//   - ActivitySourceName — manual spans inside tool implementations,
//     if any.
//
// `source=otel` resource attribute mirrors the agent-api-dotnet config so
// DD LLMObs / APM tags spans source:otel consistently on both services.
public static class TelemetrySetup
{
    public const string ActivitySourceName = "infra-advisor-mcp-server-dotnet";

    public static void Configure(WebApplicationBuilder builder)
    {
        var otlpEndpoint = Environment.GetEnvironmentVariable("OTEL_EXPORTER_OTLP_ENDPOINT")
            ?? "http://datadog-agent.datadog.svc.cluster.local:4318";
        var serviceName = Environment.GetEnvironmentVariable("OTEL_SERVICE_NAME")
            ?? "infra-advisor-mcp-server-dotnet";
        var ddEnv = Environment.GetEnvironmentVariable("DD_ENV") ?? "dev";
        var ddVersion = Environment.GetEnvironmentVariable("DD_VERSION") ?? "latest";

        builder.Services.AddOpenTelemetry()
            .ConfigureResource(r => r
                .AddService(serviceName)
                .AddAttributes(new Dictionary<string, object>
                {
                    ["deployment.environment"] = ddEnv,
                    ["service.version"]        = ddVersion,
                    ["source"]                 = "otel",
                }))
            .WithTracing(t => t
                .AddAspNetCoreInstrumentation()
                .AddHttpClientInstrumentation()
                .AddSource("Experimental.ModelContextProtocol")
                .AddSource(ActivitySourceName)
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

        builder.Logging.ClearProviders();
        builder.Logging.AddConsole();
    }
}
