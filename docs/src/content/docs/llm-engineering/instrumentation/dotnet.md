---
title: .NET instrumentation with OpenTelemetry
description: Pure-OTel instrumentation for .NET LLM apps — Microsoft Agents Framework + Microsoft.Extensions.AI + MCP, exported via OTLP to a Datadog Agent collector.
sidebar:
  order: 2
  label: .NET (OpenTelemetry)
---

import { Aside } from '@astrojs/starlight/components';

The .NET ecosystem doesn't have a ddtrace-style auto-instrumenter for LLM frameworks. Instead, **the LLM libraries themselves emit OTel spans** when you opt in. The job of `TelemetrySetup.cs` is to register those sources with the OpenTelemetry SDK and ship the result to a Datadog Agent OTel collector via OTLP.

**Source of truth in this repo:** `services/agent-api-dotnet/Observability/TelemetrySetup.cs` and `services/mcp-server-dotnet/Observability/TelemetrySetup.cs`.

<Aside type="caution">
**One tracer only.** We do **not** run the Datadog .NET tracer alongside the OTel SDK. Running both produces split trace IDs (DD reads `x-datadog-trace-id`; OTel reads W3C `traceparent`), which fragments your trace tree. Pick one and stick with it. We pick OTel because LLMObs span classification only happens on the OTLP ingest path.
</Aside>

## What gets auto-instrumented

| Library | Source name | OTel spans emitted | LLMObs span kind |
|---|---|---|---|
| `Microsoft.Extensions.AI` (M.E.AI) | `Experimental.Microsoft.Extensions.AI` | `chat`, `embeddings`, `execute_tool` | `llm`, `embedding`, `tool` |
| `Microsoft.Agents.AI` (MAF) | (your ActivitySource name) | `invoke_agent` | `agent` |
| `ModelContextProtocol(.AspNetCore)` | `Experimental.ModelContextProtocol` | MCP request / response | `tool` (client side) |
| `Npgsql.OpenTelemetry` | (built-in) | every SQL command | (no LLMObs kind; APM only) |
| ASP.NET Core | (built-in) | HTTP server / client | `workflow` (root) |

LLMObs span-kind classification (`llm`, `agent`, `tool`, `embedding`) happens on DD's OTLP ingest based on the OTel GenAI semconv operation names: `chat`, `invoke_agent`, `execute_tool`, `embeddings`. No app-side mapping required.

## Configure the SDK

```csharp
// services/agent-api-dotnet/Observability/TelemetrySetup.cs
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
                ["source"]                 = "otel",   // tells DD this is OTel-emitted
            }))
        .WithTracing(t => t
            .AddAspNetCoreInstrumentation()
            .AddHttpClientInstrumentation()
            .AddNpgsql()                                       // Postgres command spans
            .AddSource("Experimental.Microsoft.Extensions.AI") // chat / execute_tool / embeddings
            .AddSource("Experimental.ModelContextProtocol")    // MCP client + server
            .AddSource(ActivitySourceName)                     // invoke_agent + custom spans
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
}
```

The `source = otel` resource attribute matters: without it, DD tags spans `source:undefined` and the external-evaluations API won't match. Both layers (spans and evals) have to agree.

## Wire the LLM libraries to emit OTel

`AddSource(...)` just *listens* on a source name. The actual spans only flow if you opt the library into emitting them. For M.E.AI and MAF:

```csharp
// services/agent-api-dotnet/Program.cs (excerpt)
builder.Services.AddSingleton<IChatClient>(sp =>
{
    var azureClient = new AzureOpenAIClient(...);
    return azureClient.GetChatClient("gpt-4.1-mini")
        .AsIChatClient()
        .AsBuilder()
        .UseFunctionInvocation()          // emits execute_tool spans
        .UseOpenTelemetry(loggerFactory: sp.GetRequiredService<ILoggerFactory>(),
                          sourceName: "Experimental.Microsoft.Extensions.AI",
                          configure: opts => opts.EnableSensitiveData = true)
        .Build();
});

builder.Services.AddSingleton<AIAgent>(sp =>
{
    var chatClient = sp.GetRequiredService<IChatClient>();
    return new ChatClientAgent(chatClient, instructions: AgentSystemPrompt, tools: ...)
        .AsBuilder()
        .UseOpenTelemetry(sourceName: TelemetrySetup.ActivitySourceName)
        .Build();
});
```

`EnableSensitiveData = true` is required for LLMObs to capture prompt + completion content. Without it, you get tokens and timing but no message bodies — much less useful for offline debugging.

## Custom spans for steps the libraries don't cover

Anything that isn't a chat / agent / tool / embedding call is invisible to the auto-instrumentation. Wrap those manually:

```csharp
// services/agent-api-dotnet/Services/AgentService.cs (excerpt)
private static readonly ActivitySource s = new(TelemetrySetup.ActivitySourceName);

public async Task<string> ClassifyDomainTraced(string query)
{
    using var activity = s.StartActivity("classify_domain", ActivityKind.Internal);
    activity?.SetTag("dd.llmobs.span.kind", "task");          // → LLMObs `task` kind
    activity?.SetTag("gen_ai.operation.name", "classify_domain");
    activity?.SetTag("input.value", query);
    var domain = await DoClassification(query);
    activity?.SetTag("output.value", domain);
    return domain;
}
```

The `dd.llmobs.span.kind` tag is the escape hatch for span kinds that aren't in the OTel GenAI semconv yet — `task` and `retrieval` in particular. Standard kinds (`llm`, `agent`, `tool`, `embedding`) classify automatically from `gen_ai.operation.name`.

## Cross-service trace propagation

MCP calls span two services (`agent-api-dotnet` → `mcp-server-dotnet`). W3C `traceparent` propagates automatically because:
- The MCP C# SDK (≥1.3.0) inserts `traceparent` into MCP message metadata on outbound requests.
- `mcp-server-dotnet` registers `Experimental.ModelContextProtocol` as a tracing source, so its inbound MCP spans see the parent context.

If the MCP server is missing from your trace tree, the cause is almost always one of:
- MCP SDK older than 1.3.0 (the propagation didn't exist).
- Server-side `TelemetrySetup` only configured `.WithMetrics(...)` and missed `.WithTracing(...)`.
- K8s Service round-robined a follow-up request to a different replica — see [Monitoring → MCP clients](../../monitoring/mcp-clients/#session-affinity) for the `sessionAffinity: ClientIP` fix.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `source:undefined` on spans | Missing resource attribute | Add `["source"] = "otel"` to `ConfigureResource` |
| Trace tree split across two trace IDs | DD .NET tracer running alongside OTel | Remove `admission.datadoghq.com/dotnet-lib.version: v3` annotation |
| `chat` spans missing prompt/completion text | `EnableSensitiveData` not set | `.UseOpenTelemetry(configure: opts => opts.EnableSensitiveData = true)` |
| MCP server missing from trace tree | No `.WithTracing(...)` on the server | Add `.AddSource("Experimental.ModelContextProtocol")` server-side |
| APM trace → Logs tab is empty | No trace context in log JSON | Wire `DatadogTraceContextEnricher` into Serilog (see [APM correlation](../../monitoring/apm-correlation/#log-trace-correlation)) |
| Postgres host shows as a pod IP | Missing `reported_hostname` in DBM config | Set in `ad.datadoghq.com/postgres.checks` annotation |

## What's next

- [Monitoring → Spans and traces](../../monitoring/spans-and-traces/) — the trace tree this produces and how to query it.
- [Monitoring → MCP clients](../../monitoring/mcp-clients/) — the cross-service span propagation that lets one trace_id cover both services.
- [Monitoring → APM correlation](../../monitoring/apm-correlation/) — DBM and log correlation, also pure-OTel.
- [Evaluations → External](../../evaluations/external/) — the `IResponseEvaluator` pipeline this stack ships.
