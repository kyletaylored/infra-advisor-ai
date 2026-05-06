---
title: LLM Observability (.NET)
description: How the .NET Agent API manually instruments LLM Observability using OpenTelemetry gen_ai.* semantic conventions — no ddtrace auto-instrumentation available
---

The Python Agent API gets LLM Observability for free: `ddtrace` auto-instruments LangChain, LangGraph, and the OpenAI SDK, and the `LLMObs.*` decorator API creates the workflow/agent/task span hierarchy automatically.

The .NET Agent API has neither. The Datadog .NET tracer does not auto-instrument the Azure OpenAI SDK for LLM Observability, and there is no `LLMObs.*` equivalent in C#. Every span must be created explicitly using the OpenTelemetry `Activity` API with the correct `gen_ai.*` semantic convention attributes.

This page explains the full instrumentation architecture — from environment variables through export paths to the exact lines of code that produce each span.

---

## Why manual instrumentation is required

Datadog's Python tracer ships dedicated integrations for OpenAI, LangChain, and LangGraph that hook into those libraries at the call site and emit structured LLMObs spans automatically. The .NET tracer ships no equivalent integrations for Azure OpenAI or any other .NET LLM SDK.

The approach taken here is to use [OpenTelemetry Gen AI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/) (the `gen_ai.*` attribute namespace) on manually created `Activity` spans. Datadog recognises these conventions and maps them into the LLM Observability UI — the same way it would recognise spans produced by the Python SDK.

---

## Export architecture: two paths, two purposes

The .NET Agent API sends span data via two independent paths:

```
ActivitySource ("infra-advisor-agent-api-dotnet")
  │
  ├─► DD bridge (DD_TRACE_OTEL_ENABLED=true)
  │     Datadog .NET tracer acts as global OTel TracerProvider
  │     Captures all ActivitySource spans
  │     Exports to DD Agent at port 8126 (Datadog APM)
  │
  └─► Second non-global TracerProvider (TelemetrySetup.cs)
        Exports OTLP to https://api.datadoghq.com/v1/traces
        Headers: dd-api-key=<key>, dd-otlp-source=llmobs
        Datadog routes to LLM Observability (not APM)
```

The same `Activity` object is captured by both paths simultaneously. APM sees it as a distributed trace span. LLMObs sees the same span's `gen_ai.*` attributes and renders the multi-level span tree in the LLM Observability UI.

The two paths are independent — you can view the same query in APM (distributed trace from browser to MCP to gov API) and in LLM Observability (span tree focused on LLM reasoning steps) with the trace ID linking them.

---

## Environment variables

Set via `k8s/agent-api-dotnet/configmap.yaml` and the `agent-api-dotnet-secret` K8s secret:

| Variable | Value | Purpose |
|---|---|---|
| `DD_TRACE_OTEL_ENABLED` | `"true"` | DD tracer becomes global OTel TracerProvider, bridging all `ActivitySource` spans to APM |
| `DD_LLMOBS_ENABLED` | `"1"` | Enables LLM Observability feature on the DD tracer |
| `DD_LLMOBS_ML_APP` | `"infra-advisor"` | ML app name shown in LLM Observability UI |
| `DD_SITE` | `"datadoghq.com"` | Used to construct the LLMObs OTLP endpoint when `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` is not set |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | `"https://api.datadoghq.com"` | Direct OTLP endpoint for the second (LLMObs) TracerProvider |
| `DD_API_KEY` | (from K8s secret) | Injected as `dd-api-key` header on all OTLP traces to the LLMObs endpoint |
| `DD_LOGS_INJECTION` | `"true"` | DD tracer injects `dd.trace_id`/`dd.span_id` into structured log output for log-trace correlation |

---

## `TelemetrySetup.cs` — the dual TracerProvider setup

`services/agent-api-dotnet/Observability/TelemetrySetup.cs` wires up the two export paths:

```csharp
public static void Configure(WebApplicationBuilder builder)
{
    // The DD bridge (DD_TRACE_OTEL_ENABLED=true) registers itself as the global
    // OTel TracerProvider and captures all ActivitySource spans for APM.
    // No WithTracing() call needed — adding one would conflict with the DD bridge.

    // Metrics: OTLP to the on-cluster Datadog Agent.
    builder.Services.AddOpenTelemetry()
        .WithMetrics(metrics => metrics
            .AddMeter(ActivitySourceName)
            .AddOtlpExporter(otlp => {
                otlp.Endpoint = new Uri($"{otlpEndpoint}/v1/metrics");
                otlp.Protocol = OtlpExportProtocol.HttpProtobuf;
            })
        );

    // LLMObs: second non-global TracerProvider that exports the same ActivitySource spans
    // directly to Datadog LLM Observability via OTLP (bypassing the DD Agent).
    // The dd-otlp-source=llmobs header tells Datadog to route these to LLMObs, not APM.
    if (!string.IsNullOrEmpty(ddApiKey))
    {
        var llmObsProvider = Sdk.CreateTracerProviderBuilder()
            .AddSource(ActivitySourceName)           // same source as the DD bridge
            .AddOtlpExporter(otlp => {
                otlp.Endpoint = new Uri($"{llmObsEndpoint}/v1/traces");
                otlp.Headers = $"dd-api-key={ddApiKey},dd-otlp-source=llmobs";
                otlp.Protocol = OtlpExportProtocol.HttpProtobuf;
            })
            .Build();

        builder.Services.AddSingleton(llmObsProvider); // DI disposes on shutdown
    }
}
```

The key constraint: `Sdk.CreateTracerProviderBuilder()` creates a **non-global** provider. This is mandatory — calling `builder.Services.AddOpenTelemetry().WithTracing()` would attempt to register a second global provider and conflict with the DD bridge.

---

## `LlmTelemetry.cs` — the core instrumentation helper

`services/agent-api-dotnet/Observability/LlmTelemetry.cs` encapsulates every `gen_ai.*` attribute in one place. Nothing in the rest of the codebase sets `gen_ai.*` tags directly.

```csharp
public static class LlmTelemetry
{
    // Must use the same ActivitySource name as all other spans in the service.
    // Using a new name (e.g. "infra-advisor.llm") would have no registered listener
    // under DD_TRACE_OTEL_ENABLED=true and StartActivity() would silently return null.
    public static readonly ActivitySource ActivitySource =
        new(TelemetrySetup.ActivitySourceName, "1.0.0");

    public static Activity? StartLlmActivity(
        string modelName,
        string prompt,
        string taskType = "chat",
        string provider = "azure_openai",
        string? conversationId = null)
    {
        // Activity name must be "gen_ai.<operation>" per OTel Gen AI conventions.
        // The DD bridge maps gen_ai.operation.name="chat" to LLM span type "llm".
        var activity = ActivitySource.StartActivity(
            $"gen_ai.{taskType}",
            ActivityKind.Client);

        if (activity is null) return null;

        // OTel Gen AI semantic convention attributes — Datadog maps these to LLMObs fields.
        activity.SetTag("gen_ai.operation.name", "chat");
        activity.SetTag("gen_ai.system", provider);           // maps to model provider
        activity.SetTag("gen_ai.request.model", modelName);   // maps to model name
        activity.SetTag("gen_ai.prompt.0.role", "user");
        activity.SetTag("gen_ai.prompt.0.content", prompt);

        if (conversationId is not null)
            activity.SetTag("gen_ai.conversation.id", conversationId);

        // DD-specific: prompt tracking metadata for the LLMObs Prompt Tracking feature.
        activity.SetTag("_dd.ml_obs.prompt_tracking", JsonSerializer.Serialize(new {
            name = $"infra-advisor-{taskType}",
            version = "v1",
            template = prompt,
        }));

        return activity;
    }

    public static void EndLlmActivity(
        Activity? activity,
        string response,
        bool isSuccess,
        long latencyMs,
        int inputTokens = 0,
        int outputTokens = 0)
    {
        if (activity is null) return;

        activity.SetTag("gen_ai.completion.0.role", "assistant");
        activity.SetTag("gen_ai.completion.0.content", response);

        // Token counts are only set when the SDK reports non-zero values.
        if (inputTokens > 0)
            activity.SetTag("gen_ai.usage.input_tokens", inputTokens);
        if (outputTokens > 0)
            activity.SetTag("gen_ai.usage.output_tokens", outputTokens);

        activity.SetTag("llm.latency_ms", latencyMs);
        activity.SetStatus(isSuccess ? ActivityStatusCode.Ok : ActivityStatusCode.Error);
        activity.Dispose();
    }
}
```

### Why `ActivitySource.StartActivity()` can return null

`StartActivity()` returns `null` when no `ActivityListener` is registered for the named source. The DD bridge registers a listener for known sources when `DD_TRACE_OTEL_ENABLED=true`. It discovers sources that are registered before the bridge initialises.

**The critical constraint:** `LlmTelemetry.ActivitySource` must use `TelemetrySetup.ActivitySourceName` — the same name used by every other span in the service. If you use a new, distinct source name (e.g. `"infra-advisor.llm"`), the DD bridge will have no listener for it and `StartActivity()` returns `null` for every call, silently discarding all LLM spans with no error.

All `LlmTelemetry` code defensively null-checks the returned `Activity?` before calling any method on it.

---

## Span hierarchy produced

The spans produced by a single query form this tree, which maps to the LLM Observability trace view:

```
[HTTP: POST /query]                     ← auto-instrumented by DD ASP.NET Core integration
  │
  ├── [router]                           ← _activitySource.StartActivity("router")
  │     Tags: query.domain, session.id, router.specialist, router.handoff_context
  │     │
  │     └── [gen_ai.router]              ← LlmTelemetry.StartLlmActivity(taskType:"router")
  │           gen_ai.request.model       → deployment name
  │           gen_ai.prompt.0.content    → user query (first 200 chars)
  │           gen_ai.completion.0.content → chosen specialist name
  │           gen_ai.usage.input_tokens  → from CompleteChatAsync response
  │           gen_ai.usage.output_tokens → from CompleteChatAsync response
  │           llm.latency_ms             → wall-clock time for the LLM call
  │
  └── [specialist-{name}]               ← _activitySource.StartActivity("specialist-{specialist}")
        Tags: specialist, tools_available, query.domain, session.id, tools_called, sources.count
        │
        ├── [gen_ai.specialist_{name}_iter0]  ← StartLlmActivity per ReAct iteration
        │     (same gen_ai.* tags as above)
        │     gen_ai.completion.0.content     → "tool_calls" if tools invoked, else answer
        │     llm.span_type: "specialist"
        │     iteration: 0
        │
        └── [gen_ai.specialist_{name}_iter1]  ← next iteration after tool results injected
              gen_ai.completion.0.content → final answer (first 500 chars)
```

Kafka eval runs (from `KafkaConsumerService.cs`) produce the same tree, nested under an additional root span:

```
[eval.agent_run]                        ← LlmTelemetry.ActivitySource.StartActivity
  Tags: eval.query_id, eval.session_id, eval.source="kafka"
  └── [router] + [specialist-*]         ← same as above
```

This makes eval traces visually distinct from user-query traces in both APM and LLM Observability.

---

## `AgentService.cs` — call sites in detail

### Router LLM call (`RunRouterAsync`)

```csharp
var sw = Stopwatch.StartNew();

// Span opened before the LLM call.
using var llmActivity = LlmTelemetry.StartLlmActivity(
    modelName: _defaultDeployment,
    prompt: query,
    taskType: "router",
    provider: "azure");
llmActivity?.SetTag("llm.span_type", "router");

// Actual Azure OpenAI call.
var response = await chatClient.CompleteChatAsync(routerMessages, routerOptions, ct);
sw.Stop();

// Span closed with token counts from the SDK response.
LlmTelemetry.EndLlmActivity(
    activity: llmActivity,
    response: specialist,           // the chosen specialist label
    isSuccess: true,
    latencyMs: sw.ElapsedMilliseconds,
    inputTokens: response.Value.Usage?.InputTokenCount ?? 0,
    outputTokens: response.Value.Usage?.OutputTokenCount ?? 0);
```

### Specialist ReAct loop

```csharp
for (int i = 0; i < MaxIterations; i++)
{
    var iterSw = Stopwatch.StartNew();
    var promptText = /* last user message text */;

    // New span per iteration — traces each LLM call in the ReAct loop separately.
    using var llmSpan = LlmTelemetry.StartLlmActivity(
        modelName: dep,
        prompt: promptText.Length > 200 ? promptText[..200] : promptText,
        taskType: $"specialist_{specialist}_iter{i}",
        provider: "azure");
    llmSpan?.SetTag("llm.span_type", "specialist");
    llmSpan?.SetTag("specialist", specialist);
    llmSpan?.SetTag("iteration", i);

    var response = await chatClient.CompleteChatAsync(messages, options, ct);
    iterSw.Stop();

    if (completion.FinishReason == ChatFinishReason.ToolCalls)
    {
        // Model wants to call tools — close span with "tool_calls" as the response.
        LlmTelemetry.EndLlmActivity(llmSpan, "tool_calls", true,
            iterSw.ElapsedMilliseconds,
            completion.Usage?.InputTokenCount ?? 0,
            completion.Usage?.OutputTokenCount ?? 0);
        // ... dispatch tool calls via McpClientService, inject results into messages ...
    }
    else if (completion.FinishReason == ChatFinishReason.Stop)
    {
        // Model produced a final answer — close span with the answer text.
        LlmTelemetry.EndLlmActivity(llmSpan, answer.Length > 500 ? answer[..500] : answer,
            true, iterSw.ElapsedMilliseconds,
            completion.Usage?.InputTokenCount ?? 0,
            completion.Usage?.OutputTokenCount ?? 0);
        break;
    }
}
```

---

## Trace ID correlation with RUM

The `/query` response includes `trace_id` and `span_id` so the browser can construct a deep-link into Datadog APM. The `x-datadog-trace-id` header is the authoritative source:

```csharp
// In Program.cs — the helper used after every /query response.
static string? GetDdTraceId(HttpContext ctx, Activity? activity)
{
    // RUM Browser SDK injects the authoritative 64-bit decimal DD trace ID
    // into every request as x-datadog-trace-id. Use this first.
    var header = ctx.Request.Headers["x-datadog-trace-id"].FirstOrDefault();
    if (!string.IsNullOrWhiteSpace(header)) return header;

    // Fallback for non-RUM requests (test tools, direct API calls):
    // OTel TraceId is 128-bit hex; Datadog indexes by lower 64 bits as uint64 decimal.
    var hex = activity?.TraceId.ToString();
    if (hex is not { Length: 32 }) return hex;
    return ulong.TryParse(hex[16..], NumberStyles.HexNumber, null, out var lo)
        ? lo.ToString() : hex;
}
```

The `X-DD-RUM-Session-ID` header (also injected by the RUM SDK) is passed through to `AgentService.RunAgentAsync` as `rumSessionId`, where it becomes the `session.id` tag on all spans. This is what enables the **RUM Sessions → LLM Observability** link in Datadog.

---

## Prompt tracking

Each LLM span carries `_dd.ml_obs.prompt_tracking` — a JSON attribute that tells Datadog LLM Observability to associate the span with a named, versioned prompt template:

```csharp
// Set inside StartLlmActivity() for every span.
activity.SetTag("_dd.ml_obs.prompt_tracking", JsonSerializer.Serialize(new {
    name = $"infra-advisor-{taskType}",  // e.g. "infra-advisor-router"
    version = "v1",
    template = prompt,                   // truncated prompt text used as template content
}));
```

In **LLM Observability → Prompt Templates**, spans with the same `name` are grouped, allowing comparison of token usage and output quality across prompt versions.

---

## Faithfulness score

Unlike Python (which uses `LLMObs.submit_evaluation()`), the .NET stack records faithfulness as an OTel **metric** rather than a LLMObs evaluation:

```csharp
// In AgentService constructor:
_faithfulnessHistogram = meter.CreateHistogram<double>(
    "agent.faithfulness_score",
    description: "Faithfulness evaluation score for agent responses");

// After every query, fire-and-forget:
_ = Task.Run(async () =>
{
    // gpt-4.1-nano scores answer faithfulness 0.0–1.0
    if (double.TryParse(scoreText, ..., out var score))
    {
        score = Math.Clamp(score, 0.0, 1.0);
        _faithfulnessHistogram.Record(score,
            new KeyValuePair<string, object?>("session.id", capturedSessionId),
            new KeyValuePair<string, object?>("query.domain", capturedDomain));
    }
});
```

The histogram is exported via the metrics OTLP path to the Datadog Agent and appears as `agent.faithfulness_score` in Datadog Metrics. The faithfulness monitor (`datadog/monitors/faithfulness-score.json`) alerts when the mean drops below 0.75 over a 1-hour window.

> The Python stack submits faithfulness directly to LLMObs via `LLMObs.submit_evaluation()`, which makes it appear under the Evaluations tab on the specific span. In .NET, the score is a separate metric — it correlates by `session.id` and `query.domain` dimensions but does not attach to a specific LLMObs span.

---

## Differences from the Python stack

| Concern | Python (.NET tracer + LLMObs) | .NET (OTel + manual) |
|---|---|---|
| LLM call instrumentation | `ddtrace` auto-instruments Azure OpenAI SDK calls | Manually wrap each call in `LlmTelemetry.StartLlmActivity` / `EndLlmActivity` |
| Span hierarchy | `LLMObs.workflow()` / `.agent()` / `.task()` / `.llm()` decorators | `ActivitySource.StartActivity()` for orchestration spans; `LlmTelemetry` for LLM spans |
| Export to LLMObs | `ddtrace` routes LLMObs spans to DD Agent automatically | Second non-global `TracerProvider` with `dd-otlp-source=llmobs` OTLP header |
| Token counting | Auto-extracted by ddtrace LangChain integration | Read from `response.Value.Usage?.InputTokenCount` / `OutputTokenCount` and passed to `EndLlmActivity` |
| Faithfulness | `LLMObs.submit_evaluation("faithfulness", score)` — attaches to span in LLMObs UI | OTel `Histogram<double>` metric — appears in Datadog Metrics, correlated by dimension |
| Prompt tracking | Not applicable (Python prompts tracked via LangChain auto-instrumentation) | `_dd.ml_obs.prompt_tracking` JSON attribute set manually in `StartLlmActivity` |
| Session linking to RUM | `session.id` set via `LLMObs.agent()` context | `session.id` tag set on `ActivitySource` spans; `X-DD-RUM-Session-ID` header read in `/query` handler |

---

## Debugging spans in production

**Verify spans are reaching Datadog APM:**

```bash
kubectl logs -n infra-advisor deploy/agent-api-dotnet --tail=50 | grep -E "trace|span|gen_ai"
```

Look for log lines with `dd.trace_id` and `dd.span_id` fields — `DD_LOGS_INJECTION=true` injects these into every structured log entry.

**Verify LLM spans are non-null:**

If `StartActivity()` returns `null`, no spans are emitted. Verify the `ActivitySource` name matches: it must be `"infra-advisor-agent-api-dotnet"` (the value of `TelemetrySetup.ActivitySourceName`). Any other name will produce null spans under the DD bridge unless explicitly registered with `AddSource()` in a TracerProvider.

**Verify the LLMObs OTLP export:**

The second TracerProvider only initialises when `DD_API_KEY` is non-empty in the pod environment. Confirm the secret is mounted:

```bash
kubectl exec -n infra-advisor deploy/agent-api-dotnet -- env | grep DD_API_KEY
```
