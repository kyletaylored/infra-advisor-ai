using Microsoft.AspNetCore.Builder;
using OpenTelemetry.Exporter;
using OpenTelemetry.Resources;
using OpenTelemetry.Metrics;

namespace InfraAdvisor.McpServer.Observability;

public static class TelemetrySetup
{
    // DD SDK captures all ActivitySource spans when DD_TRACE_OTEL_ENABLED=true
    public const string ActivitySourceName = "infra-advisor-mcp-server-dotnet";

    public static void Configure(WebApplicationBuilder builder)
    {
        var otlpEndpoint = Environment.GetEnvironmentVariable("OTEL_EXPORTER_OTLP_ENDPOINT")
            ?? "http://datadog-agent.datadog.svc.cluster.local:4318";
        var serviceName = Environment.GetEnvironmentVariable("OTEL_SERVICE_NAME")
            ?? "infra-advisor-mcp-server-dotnet";
        var ddEnv = Environment.GetEnvironmentVariable("DD_ENV") ?? "dev";
        var ddVersion = Environment.GetEnvironmentVariable("DD_VERSION") ?? "latest";

        // Traces: DD SDK (auto-injected + DD_TRACE_OTEL_ENABLED=true) handles export to port 8126.
        // Metrics: OTLP to DDOT collector for any custom meters.
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

        // DD_LOGS_INJECTION=true injects trace context into ILogger structured properties
        // for log/trace correlation via Datadog agent stdout collection.
        builder.Logging.ClearProviders();
        builder.Logging.AddConsole();
    }
}
