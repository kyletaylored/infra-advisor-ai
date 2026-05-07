---
title: LLM Observability (.NET)
description: Exact OTel attribute mapping for .NET LLM Observability — every attribute traced back to the Datadog spec
---

import { Aside } from '@astrojs/starlight/components';

<Aside type="tip">
**Spec source:** [Datadog OTel LLM Observability instrumentation](https://docs.datadoghq.com/llm_observability/instrumentation/otel_instrumentation) — every attribute choice below traces back to this page.
</Aside>

The Python Agent API gets LLM Observability automatically via `ddtrace` auto-instrumentation of LangChain and the OpenAI SDK. The .NET Agent API has no equivalent — every span is created explicitly using the OpenTelemetry `Activity` API. This page documents exactly which attributes are set, why each one is required, and what the Datadog backend does with them.

---

## Export path

```
ActivitySource("infra-advisor-agent-api-dotnet")  ← SINGLE shared instance (DI singleton)
  │
  ├─► DD bridge (DD_TRACE_OTEL_ENABLED=true)
  │     .NET tracer becomes global OTel TracerProvider
  │     Exports to DD Agent :8126 → Datadog APM
  │
  └─► Non-global OTLP TracerProvider  [TelemetrySetup.cs]
        POST https://otlp.us3.datadoghq.com/v1/traces
        Headers: dd-api-key=<key>, dd-otlp-source=llmobs
        → Datadog LLM Observability (NOT APM)
```

The `dd-otlp-source=llmobs` header is the routing key. Without it, spans go to APM. The same `Activity` object is captured by both paths simultaneously.

<Aside type="caution">
**Two endpoint pitfalls:**
1. Host: use `otlp.{DD_SITE}` not `api.{DD_SITE}` — `api.` silently drops all LLMObs payloads
2. Site: US3 is `us3.datadoghq.com`, US1 is `datadoghq.com` — mixing sites sends spans to the wrong org

**ActivitySource must be a single shared instance.** Two `ActivitySource` objects with the same name produce disconnected trace contexts — spans from one instance appear as "span links" in APM instead of children of the HTTP request span. `Program.cs` registers `LlmTelemetry.ActivitySource` as the DI singleton and all `LlmTelemetry` methods accept it as a parameter.
</Aside>

**Required env vars** (`k8s/agent-api-dotnet/configmap.yaml` + secret):

| Variable | Value | Why |
|---|---|---|
| `DD_TRACE_OTEL_ENABLED` | `true` | Activates the DD bridge as global TracerProvider |
| `DD_LLMOBS_ENABLED` | `1` | Enables LLMObs feature on the .NET tracer |
| `DD_LLMOBS_ML_APP` | `infra-advisor` | ML app label in LLMObs UI |
| `DD_SITE` | `us3.datadoghq.com` | US3 site — used as fallback for OTLP endpoint construction |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | `https://otlp.us3.datadoghq.com` | Direct OTLP endpoint for the LLMObs provider |
| `DD_API_KEY` | (K8s secret) | Injected as `dd-api-key` OTLP header |

---

## Span kind resolution

Datadog determines the LLM Observability span kind from `gen_ai.operation.name`. This is the **only** supported mechanism for OTLP spans — the mapping is exact:

| `gen_ai.operation.name` value | LLMObs span kind |
|---|---|
| `chat`, `text_completion`, `completion`, `generate_content` | **llm** |
| `invoke_agent`, `create_agent` | **agent** |
| `execute_tool` | **tool** |
| `embeddings`, `embedding` | **embedding** |
| `rerank` or **absent/unrecognised** | **workflow** |

`dd.llmobs.span.kind` is also set as a belt-and-suspenders hint for any fallback logic DD may apply.

**Common mistake:** Using `"run_agent"` as the operation name maps to **workflow** (unrecognised default), not **agent**. Must be `"invoke_agent"` or `"create_agent"`.

---

## Session linking

**Spec attribute:** `gen_ai.conversation.id`

This is the **only** attribute the Datadog LLMObs backend reads for session correlation. Setting a custom tag like `session.id` makes it a plain `key:value` tag — it appears in tag search but does **not** populate the Sessions view.

Every span (`StartAgentActivity`, `StartLlmActivity`, `TagWorkflow`) sets `gen_ai.conversation.id` to `obsSessionId` (which is `rumSessionId ?? sessionId`).

---

## Input and output messages

**Spec attributes:** `gen_ai.input.messages` and `gen_ai.output.messages`

Both must be **JSON-serialised arrays** of message objects. The format the backend reads:

```json
[{"role": "user", "content": "..."}]
[{"role": "assistant", "content": "..."}]
```

**What does NOT work:**
- `gen_ai.prompt.0.role` / `gen_ai.prompt.0.content` — not read by OTLP ingestion
- `gen_ai.completion.0.role` / `gen_ai.completion.0.content` — not read by OTLP ingestion

**Two delivery paths (both used for belt-and-suspenders):**

1. **Direct span attributes** (preferred): `activity.SetTag("gen_ai.input.messages", jsonString)`
2. **Span events** (belt-and-suspenders): `activity.AddEvent(new ActivityEvent("gen_ai.client.inference.operation.details", tags: ...))` with `gen_ai.input.messages` / `gen_ai.output.messages` as event attributes

Both paths are used in `LlmTelemetry.cs`. The event name must be exactly `gen_ai.client.inference.operation.details` — other names are ignored by the DD backend.

---

## Provider identification

**Primary:** `gen_ai.provider.name` (spec primary attribute)  
**Fallback:** `gen_ai.system` (read if `gen_ai.provider.name` is absent)

Both are set to `"azure_openai"` on every LLM span.

---

## Filtered tag namespaces

The following tag prefixes are **silently dropped** by the DD LLMObs backend and will never appear in the UI or evaluations:

- `_dd.*`
- `llm.*` ← **this means `llm.latency_ms` is silently dropped**
- `ddtags`
- `events`

Latency is tracked as `gen_ai.latency_ms` (outside the filtered namespaces).

---

## `LlmTelemetry.cs` — attribute reference

`services/agent-api-dotnet/Observability/LlmTelemetry.cs`

### `StartAgentActivity(source, agentName, query, sessionId)`

Creates the top-level span that wraps the entire query lifecycle. `source` must be the DI singleton — **not** `LlmTelemetry.ActivitySource` directly — so the span is parented under the HTTP request Activity that the DD bridge is already tracking.

```csharp
var activity = source.StartActivity("invoke_agent", ActivityKind.Internal);
```

| Attribute | Value | Spec basis |
|---|---|---|
| `gen_ai.operation.name` | `"invoke_agent"` | Maps to **agent** span kind |
| `gen_ai.agent.name` | `"infra-advisor"` | OTel GenAI agent spans spec |
| `gen_ai.conversation.id` | `sessionId` | Maps to `session_id` in LLMObs |
| `dd.llmobs.span.kind` | `"agent"` | Belt-and-suspenders hint |
| `ml_app` | `"infra-advisor"` | LLMObs ML app grouping |
| `gen_ai.input.messages` | `[{"role":"user","content":query}]` (JSON) | Input for agent span — also emitted as span event |

### `EndAgentActivity(activity, answer, isSuccess)`

```csharp
activity.SetTag("gen_ai.output.messages",
    JsonSerializer.Serialize(new[] { new { role = "assistant", content = answer } }));
activity.AddEvent(new ActivityEvent("gen_ai.client.inference.operation.details",
    tags: new ActivityTagsCollection(new[] {
        KeyValuePair.Create<string, object?>("gen_ai.output.messages", outputJson)
    })
));
```

| Attribute | Value | Spec basis |
|---|---|---|
| `gen_ai.output.messages` | `[{"role":"assistant","content":answer}]` (JSON) | Output for agent span — also emitted as span event |

Disposed by the caller's `using` block — `EndAgentActivity` does **not** call `Dispose()`.

### `TagWorkflow(activity, sessionId)`

Tags the `router` and `specialist-*` spans as workflow kind. No `gen_ai.operation.name` is set (absent value → workflow default).

| Attribute | Value | Spec basis |
|---|---|---|
| `gen_ai.conversation.id` | `sessionId` | Session linking |
| `dd.llmobs.span.kind` | `"workflow"` | Explicit hint (no op name maps to workflow) |
| `ml_app` | `"infra-advisor"` | LLMObs ML app grouping |

### `StartLlmActivity(source, modelName, prompt, sessionId, operation, provider, name)`

Creates one span per `CompleteChatAsync` call.

```csharp
var activity = ActivitySource.StartActivity(
    name ?? $"{operation} {modelName}",
    ActivityKind.Client);  // Client = outgoing request
```

| Attribute | Value | Spec basis |
|---|---|---|
| `gen_ai.operation.name` | `"chat"` | Maps to **llm** span kind |
| `gen_ai.provider.name` | `"azure_openai"` | Primary provider attribute |
| `gen_ai.system` | `"azure_openai"` | Fallback provider attribute |
| `gen_ai.request.model` | deployment name | Model identifier in LLMObs |
| `gen_ai.conversation.id` | `sessionId` | Session linking |
| `dd.llmobs.span.kind` | `"llm"` | Belt-and-suspenders hint |
| `ml_app` | `"infra-advisor"` | LLMObs ML app grouping |
| `gen_ai.input.messages` | `[{"role":"user","content":prompt}]` (JSON) | Input shown in LLMObs — also emitted as span event |

### `EndLlmActivity(activity, response, isSuccess, latencyMs, inputTokens, outputTokens, finishReason)`

```csharp
activity.SetTag("gen_ai.output.messages",
    JsonSerializer.Serialize(new[] { new { role = "assistant", content = response } }));
activity.AddEvent(new ActivityEvent("gen_ai.client.inference.operation.details",
    tags: new ActivityTagsCollection(new[] {
        KeyValuePair.Create<string, object?>("gen_ai.output.messages", outputJson)
    })
));
```

| Attribute | Value | Spec basis |
|---|---|---|
| `gen_ai.output.messages` | `[{"role":"assistant","content":response}]` (JSON) | Output shown in LLMObs — also emitted as span event |
| `gen_ai.usage.input_tokens` | `inputTokens` (if > 0) | Token count from SDK |
| `gen_ai.usage.output_tokens` | `outputTokens` (if > 0) | Token count from SDK |
| `gen_ai.response.finish_reasons` | `["stop"]` or `["tool_calls"]` (JSON array) | Finish reason per spec |
| `gen_ai.latency_ms` | elapsed ms | Custom — avoids `llm.*` filtered namespace |

Calls `activity.Dispose()` directly (callers use `using var` which double-disposes harmlessly — `Activity.Dispose()` is idempotent).

---

## Span hierarchy produced per query

```
invoke_agent  [kind=agent]
  gen_ai.conversation.id = obsSessionId
  gen_ai.input.messages  = [{"role":"user","content":query}]
  gen_ai.output.messages = [{"role":"assistant","content":answer}]
  │
  ├── router  [kind=workflow]
  │     gen_ai.conversation.id = obsSessionId
  │     query.domain, router.specialist, router.handoff_context
  │     │
  │     └── chat gpt-4.1-mini  [kind=llm, ActivityKind.Client]
  │           gen_ai.operation.name    = "chat"
  │           gen_ai.provider.name     = "azure_openai"
  │           gen_ai.system            = "azure_openai"
  │           gen_ai.request.model     = "gpt-4.1-mini"
  │           gen_ai.conversation.id   = obsSessionId
  │           gen_ai.input.messages    = [{"role":"user","content":query}]
  │           gen_ai.output.messages   = [{"role":"assistant","content":"engineering"}]
  │           gen_ai.usage.input_tokens / output_tokens
  │
  └── specialist-engineering  [kind=workflow]
        gen_ai.conversation.id = obsSessionId
        specialist, tools_available, tools_called, sources.count
        │
        ├── chat gpt-4.1-mini  [kind=llm, iter=0]
        │     gen_ai.output.messages = [{"role":"assistant","content":"tool_calls"}]
        │     gen_ai.response.finish_reasons = ["tool_calls"]
        │
        └── chat gpt-4.1-mini  [kind=llm, iter=1]
              gen_ai.output.messages = [{"role":"assistant","content":answer}]
              gen_ai.response.finish_reasons = ["stop"]
```

---

## `TelemetrySetup.cs` — non-global provider

`services/agent-api-dotnet/Observability/TelemetrySetup.cs`

The LLMObs OTLP provider is **non-global** (`Sdk.CreateTracerProviderBuilder()`, not `AddOpenTelemetry().WithTracing()`). A global provider would conflict with the DD bridge. The non-global provider only fires when `DD_API_KEY` is non-empty:

```csharp
if (!string.IsNullOrEmpty(ddApiKey))
{
    var llmObsProvider = Sdk.CreateTracerProviderBuilder()
        .AddSource(ActivitySourceName)          // same name as DD bridge
        .ConfigureResource(r => r.AddService(serviceName)...)
        .AddOtlpExporter(otlp => {
            otlp.Endpoint = new Uri($"{llmObsEndpoint.TrimEnd('/')}/v1/traces");
            otlp.Headers  = $"dd-api-key={ddApiKey},dd-otlp-source=llmobs";
            otlp.Protocol = OtlpExportProtocol.HttpProtobuf;
        })
        .Build();
    builder.Services.AddSingleton(llmObsProvider); // DI disposes on shutdown
}
```

`llmObsEndpoint` resolves to `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` env var (configmap: `https://otlp.datadoghq.com`) or falls back to `https://otlp.{DD_SITE}`. The code appends `/v1/traces`, giving the final URL `https://otlp.datadoghq.com/v1/traces`.

**Why the same `ActivitySourceName`:** A different source name would require its own listener registration. Since the DD bridge automatically captures `ActivitySourceName`, sharing it means both providers see all spans without duplication in either sink.

---

## `AgentService.cs` — call sites

`services/agent-api-dotnet/Services/AgentService.cs`

```csharp
// Line ~234 — top of RunAgentAsync, after obsSessionId is computed:
using var agentActivity = LlmTelemetry.StartAgentActivity("infra-advisor", query, obsSessionId);

// Line ~241 — router workflow span:
using (var routerActivity = _activitySource.StartActivity("router"))
{
    LlmTelemetry.TagWorkflow(routerActivity, obsSessionId);
    ...
    (specialist, handoffContext) = await RunRouterAsync(query, chatClient, obsSessionId, ct);
}

// Line ~297 — specialist workflow span:
using (var specialistActivity = _activitySource.StartActivity($"specialist-{specialist}"))
{
    LlmTelemetry.TagWorkflow(specialistActivity, obsSessionId);
    ...
    // ReAct loop — one LLM span per iteration:
    using var llmSpan = LlmTelemetry.StartLlmActivity(
        modelName: dep,
        prompt: promptText.Length > 500 ? promptText[..500] : promptText,
        sessionId: obsSessionId,
        provider: "azure_openai");
    ...
    LlmTelemetry.EndLlmActivity(llmSpan, response, isSuccess, elapsedMs,
        completion.Usage?.InputTokenCount ?? 0,
        completion.Usage?.OutputTokenCount ?? 0,
        finishReason: "tool_calls" | "stop");
}

// After specialist block — record output on the agent span before disposal:
LlmTelemetry.EndAgentActivity(agentActivity, answer, isSuccess: !string.IsNullOrEmpty(answer));
// agentActivity disposed here by using block
```

```csharp
// RunRouterAsync — receives sessionId so the router LLM span is session-tagged:
private async Task<(string, string)> RunRouterAsync(
    string query, ChatClient chatClient, string sessionId, CancellationToken ct)
{
    using var llmActivity = LlmTelemetry.StartLlmActivity(
        modelName: _defaultDeployment,
        prompt: query,
        sessionId: sessionId,
        provider: "azure_openai",
        name: "chat router");              // stable span name instead of dynamic taskType
    ...
    LlmTelemetry.EndLlmActivity(llmActivity, specialist, true, elapsed,
        response.Value.Usage?.InputTokenCount ?? 0,
        response.Value.Usage?.OutputTokenCount ?? 0);
}
```

---

## Trace ID correlation with RUM

`x-datadog-trace-id` header (injected by RUM Browser SDK) is the authoritative 64-bit decimal DD trace ID. It is read in the `/query` handler in `Program.cs` and returned to the browser so the UI can construct an APM deep-link.

`X-DD-RUM-Session-ID` (also injected by RUM) becomes `rumSessionId`, which flows to `obsSessionId` and then to `gen_ai.conversation.id` on every span.

---

## Differences from the Python stack

| Concern | Python | .NET |
|---|---|---|
| LLM call instrumentation | `ddtrace` auto-instruments LangChain/OpenAI SDK | Manual `LlmTelemetry.StartLlmActivity` / `EndLlmActivity` per call |
| Span hierarchy | `LLMObs.workflow()` / `.agent()` / `.llm()` decorators | `ActivitySource.StartActivity()` + `LlmTelemetry` helpers |
| Session linking | `LLMObs.agent()` `session_id` param | `gen_ai.conversation.id` tag on every Activity |
| Input/output format | LLMObs SDK handles internally | `gen_ai.input.messages` / `gen_ai.output.messages` JSON arrays + span events |
| Export path to LLMObs | `ddtrace` routes automatically | Non-global OTLP provider with `dd-otlp-source=llmobs` header |
| Faithfulness evaluation | `LLMObs.submit_evaluation()` — attaches to span | OTel `Histogram<double>` metric `agent.faithfulness_score` — correlates by dimension |

---

## Debugging checklist

**1. Verify endpoint is correct** (root cause of zero LLM traces if wrong):
```bash
kubectl get configmap agent-api-dotnet-config -n infra-advisor -o jsonpath='{.data.OTEL_EXPORTER_OTLP_TRACES_ENDPOINT}'
# Must print: https://otlp.us3.datadoghq.com   (US3)
# NOT:        https://api.datadoghq.com          (wrong host)
# NOT:        https://otlp.datadoghq.com          (wrong site — US1 not US3)

kubectl get configmap agent-api-dotnet-config -n infra-advisor -o jsonpath='{.data.DD_SITE}'
# Must print: us3.datadoghq.com
```

**2. Check pod has DD_API_KEY** (required for the LLMObs OTLP provider to initialise):
```bash
kubectl exec -n infra-advisor deploy/agent-api-dotnet -- env | grep DD_API_KEY
```

**3. Check spans reach APM** (confirms DD bridge is working):
```bash
kubectl logs -n infra-advisor deploy/agent-api-dotnet --tail=100 | grep dd.trace_id
```

**4. Verify span kind** by querying APM for `gen_ai.operation.name:invoke_agent` — if the agent span appears in APM but not LLMObs, the OTLP provider is not initialising (missing `DD_API_KEY` or wrong endpoint).

**Expected 3–5 minute delay** before spans appear in LLMObs after first deploy.

### Common silent failures

| Symptom | Root cause |
|---|---|
| No LLM traces at all | Wrong OTLP endpoint host (`api.` instead of `otlp.`) or wrong site (`datadoghq.com` instead of `us3.datadoghq.com`) |
| Spans in APM but not LLMObs | Missing `DD_API_KEY` or non-global OTLP provider not initialising |
| LLM spans appear as "span links" | Two `ActivitySource` instances with the same name — spans start new traces instead of inheriting HTTP context |
| Sessions not appearing | Using `session.id` instead of `gen_ai.conversation.id` |
| Spans appear as "workflow" not "llm" | Wrong `gen_ai.operation.name` (e.g. `"run_agent"` instead of `"invoke_agent"`) |
| Input/output missing in LLMObs | Using `gen_ai.prompt.0.*` / `gen_ai.completion.0.*` attribute names |
| Tags silently missing | Using `llm.*` or `_dd.*` prefix — filtered by DD backend |
