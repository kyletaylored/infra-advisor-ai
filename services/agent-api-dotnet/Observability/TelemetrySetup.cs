using Microsoft.AspNetCore.Builder;
using OpenInference.NET.Extensions.DependencyInjection;
using OpenTelemetry.Exporter;
using OpenTelemetry.Resources;
using OpenTelemetry.Metrics;

namespace InfraAdvisor.AgentApi.Observability;

public static class TelemetrySetup
{
    // Used by AgentService for custom spans; DD SDK captures all ActivitySource spans
    // when DD_TRACE_OTEL_ENABLED=true (no OTel TracerProvider registration needed)
    public const string ActivitySourceName = "infra-advisor-agent-api-dotnet";
    public const string OpenInferenceSourceName = "OpenInference.NET";

    public static void Configure(WebApplicationBuilder builder)
    {
        var otlpEndpoint = Environment.GetEnvironmentVariable("OTEL_EXPORTER_OTLP_ENDPOINT")
            ?? "http://datadog-agent.datadog.svc.cluster.local:4318";
        var serviceName = Environment.GetEnvironmentVariable("OTEL_SERVICE_NAME")
            ?? "infra-advisor-agent-api-dotnet";
        var ddEnv = Environment.GetEnvironmentVariable("DD_ENV") ?? "dev";
        var ddVersion = Environment.GetEnvironmentVariable("DD_VERSION") ?? "latest";

        // OpenInference.NET: configures global LlmTelemetry options + registers DI services.
        // Spans are emitted under ActivitySource "OpenInference.NET" and captured automatically
        // by the DD SDK bridge (DD_TRACE_OTEL_ENABLED=true).
        builder.Services.AddOpenInferenceTelemetry(options => {
            options.EmitTextContent = true;
            options.RecordTokenUsage = true;
            options.SanitizeSensitiveInfo = true;
            options.RecordModelName = true;
            options.EmitMetrics = true;
        });

        // Traces: DD SDK bridge (DD_TRACE_OTEL_ENABLED=true) captures all ActivitySources.
        // Metrics: OTLP to DDOT collector for both custom and OpenInference meters.
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
                .AddMeter(OpenInferenceSourceName)
                .AddOtlpExporter(otlp => {
                    otlp.Endpoint = new Uri($"{otlpEndpoint.TrimEnd('/')}/v1/metrics");
                    otlp.Protocol = OtlpExportProtocol.HttpProtobuf;
                })
            );

        // Console logging only; DD_LOGS_INJECTION=true (set in configmap) causes the DD SDK
        // to inject dd.trace_id/dd.span_id into ILogger structured properties so the Datadog
        // agent can correlate stdout logs with APM traces.
        builder.Logging.ClearProviders();
        builder.Logging.AddConsole(opts => opts.FormatterName = "simple");
    }
}
