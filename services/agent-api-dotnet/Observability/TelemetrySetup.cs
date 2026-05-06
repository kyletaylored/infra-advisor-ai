using Microsoft.AspNetCore.Builder;
using OpenInference.NET.Extensions.DependencyInjection;
using OpenTelemetry;
using OpenTelemetry.Exporter;
using OpenTelemetry.Resources;
using OpenTelemetry.Metrics;
using OpenTelemetry.Trace;

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

        // LLMObs: second non-global TracerProvider — sends gen_ai.* spans to DD LLM Observability
        // via direct OTLP. The DD bridge (global provider) handles APM; this handles LLMObs.
        // DD_API_KEY is already injected via the agent-api-dotnet-secret K8s secret.
        var ddApiKey = Environment.GetEnvironmentVariable("DD_API_KEY") ?? "";
        var ddSite = Environment.GetEnvironmentVariable("DD_SITE") ?? "datadoghq.com";
        var llmObsEndpoint = Environment.GetEnvironmentVariable("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
            ?? $"https://api.{ddSite}";

        if (!string.IsNullOrEmpty(ddApiKey))
        {
            var llmObsProvider = Sdk.CreateTracerProviderBuilder()
                .AddSource(ActivitySourceName)
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
            // Register for DI-managed disposal on app shutdown.
            builder.Services.AddSingleton(llmObsProvider);
        }

        // Console logging only; DD_LOGS_INJECTION=true (set in configmap) causes the DD SDK
        // to inject dd.trace_id/dd.span_id into ILogger structured properties so the Datadog
        // agent can correlate stdout logs with APM traces.
        builder.Logging.ClearProviders();
        builder.Logging.AddConsole(opts => opts.FormatterName = "simple");
    }
}
