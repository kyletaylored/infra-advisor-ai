using Microsoft.AspNetCore.Builder;
using OpenTelemetry.Exporter;
using OpenTelemetry.Logs;
using OpenTelemetry.Resources;
using OpenTelemetry.Trace;
using OpenTelemetry.Metrics;

namespace InfraAdvisor.McpServer.Observability;

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

        builder.Logging.ClearProviders();
        builder.Logging.AddConsole();
        builder.Logging.AddOpenTelemetry(logging =>
        {
            logging.IncludeFormattedMessage = true;
            logging.IncludeScopes = true;
            logging.ParseStateValues = true;
            logging.AddOtlpExporter(otlp =>
            {
                otlp.Endpoint = new Uri(otlpEndpoint);
                otlp.Protocol = OtlpExportProtocol.HttpProtobuf;
            });
        });
    }
}
