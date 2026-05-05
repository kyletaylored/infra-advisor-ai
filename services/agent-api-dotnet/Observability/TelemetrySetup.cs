using OpenTelemetry.Exporter;
using OpenTelemetry.Resources;
using OpenTelemetry.Trace;
using OpenTelemetry.Metrics;
using Npgsql;

namespace InfraAdvisor.AgentApi.Observability;

public static class TelemetrySetup
{
    public const string ServiceName = "infraadvisor-agent-api-dotnet";
    public const string ActivitySourceName = "infraadvisor-agent-api-dotnet";
    // OpenInference.NET emits spans under this source name
    public const string OpenInferenceSourceName = "OpenInference.NET";

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
                .AddNpgsql()
                .AddSource(ActivitySourceName)
                // OpenInference.NET LLM spans
                .AddSource(OpenInferenceSourceName)
                .AddOtlpExporter(otlp => {
                    otlp.Endpoint = new Uri(otlpEndpoint);
                    otlp.Protocol = OtlpExportProtocol.HttpProtobuf;
                })
            )
            .WithMetrics(metrics => metrics
                .AddAspNetCoreInstrumentation()
                .AddHttpClientInstrumentation()
                .AddMeter(ActivitySourceName)
                .AddOtlpExporter(otlp => {
                    otlp.Endpoint = new Uri(otlpEndpoint);
                    otlp.Protocol = OtlpExportProtocol.HttpProtobuf;
                })
            );
    }
}
