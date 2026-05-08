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
    // Both the DD bridge ActivityListener (DD_TRACE_OTEL_ENABLED=true → APM) and the
    // hosted OTel TracerProvider (LLMObs OTLP) register this source so all spans reach both sinks.
    public const string ActivitySourceName = "infra-advisor-agent-api-dotnet";

    public static void Configure(WebApplicationBuilder builder)
    {
        var otlpEndpoint = Environment.GetEnvironmentVariable("OTEL_EXPORTER_OTLP_ENDPOINT")
            ?? "http://datadog-agent.datadog.svc.cluster.local:4318";
        var serviceName = Environment.GetEnvironmentVariable("OTEL_SERVICE_NAME")
            ?? "infra-advisor-agent-api-dotnet";
        var ddEnv = Environment.GetEnvironmentVariable("DD_ENV") ?? "dev";
        var ddVersion = Environment.GetEnvironmentVariable("DD_VERSION") ?? "latest";
        var ddApiKey = Environment.GetEnvironmentVariable("DD_API_KEY") ?? "";
        var ddSite = Environment.GetEnvironmentVariable("DD_SITE") ?? "datadoghq.com";
        var llmObsEndpoint = Environment.GetEnvironmentVariable("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
            ?? $"https://otlp.{ddSite}";

        var otelBuilder = builder.Services.AddOpenTelemetry()
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

        // LLMObs: use the hosting integration's .WithTracing() so ASP.NET Core properly
        // manages the BatchExportProcessor lifecycle via its IHostedService infrastructure.
        // The previous Sdk.CreateTracerProviderBuilder() (non-global) pattern created the
        // provider as a DI singleton but its background export loop never started correctly
        // alongside DD_TRACE_OTEL_ENABLED=true — evidenced by zero OtlpTraceExporter HTTP
        // calls in pod logs despite active queries. The metrics exporter (identical setup via
        // .WithMetrics()) works because it goes through this same hosting path.
        //
        // AlwaysOnSampler: forces recording regardless of the DD bridge HTTP span's
        // sampling flag so UI-originated gen_ai.* spans are never silently dropped.
        // The DD bridge (DD_TRACE_OTEL_ENABLED=true) coexists as a separate ActivityListener
        // and continues to route spans to APM; this provider routes to LLMObs via OTLP.
        if (!string.IsNullOrEmpty(ddApiKey))
        {
            otelBuilder.WithTracing(traces => traces
                .AddSource(ActivitySourceName)
                .SetSampler(new AlwaysOnSampler())
                .AddOtlpExporter(otlp => {
                    otlp.Endpoint = new Uri($"{llmObsEndpoint.TrimEnd('/')}/v1/traces");
                    otlp.Headers = $"dd-api-key={ddApiKey},dd-otlp-source=llmobs";
                    otlp.Protocol = OtlpExportProtocol.HttpProtobuf;
                })
            );
        }

        builder.Logging.ClearProviders();
        builder.Logging.AddConsole(opts => opts.FormatterName = "simple");
    }
}
