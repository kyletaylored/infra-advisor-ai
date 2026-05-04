using OpenTelemetry.Exporter;
using OpenTelemetry.Resources;
using OpenTelemetry.Trace;
using OpenTelemetry.Metrics;

namespace InfraAdvisor.McpServer.Observability;

public static class TelemetrySetup
{
    public const string ServiceName = "infratools-mcp-dotnet";
    public const string ActivitySourceName = "infratools-mcp-dotnet";

    public static void Configure(IServiceCollection services)
    {
        var otlpEndpoint = Environment.GetEnvironmentVariable("OTEL_EXPORTER_OTLP_ENDPOINT")
            ?? "http://datadog-agent.datadog.svc.cluster.local:4318";
        var ddEnv = Environment.GetEnvironmentVariable("DD_ENV") ?? "dev";
        var ddVersion = Environment.GetEnvironmentVariable("DD_VERSION") ?? "latest";

        services.AddOpenTelemetry()
            .ConfigureResource(r => r
                .AddService(ServiceName)
                .AddAttributes(new Dictionary<string, object> {
                    ["deployment.environment"] = ddEnv,
                    ["service.version"] = ddVersion,
                })
            )
            .WithTracing(tracing => tracing
                .AddAspNetCoreInstrumentation(opts => opts.RecordException = true)
                .AddHttpClientInstrumentation()
                .AddSource(ActivitySourceName)
                .AddOtlpExporter(otlp =>
                {
                    otlp.Endpoint = new Uri(otlpEndpoint);
                    otlp.Protocol = OtlpExportProtocol.HttpProtobuf;
                })
            )
            .WithMetrics(metrics => metrics
                .AddAspNetCoreInstrumentation()
                .AddHttpClientInstrumentation()
                .AddMeter(ActivitySourceName)
                .AddOtlpExporter(otlp =>
                {
                    otlp.Endpoint = new Uri(otlpEndpoint);
                    otlp.Protocol = OtlpExportProtocol.HttpProtobuf;
                })
            );
    }
}
