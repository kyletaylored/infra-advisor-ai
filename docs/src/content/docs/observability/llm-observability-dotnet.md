---
title: AI Engineering Guide — OpenTelemetry for .NET LLM apps
description: A conceptual how-to for instrumenting .NET LLM applications end-to-end with OpenTelemetry and surfacing the data in Datadog LLM Observability.
---

import { Aside } from '@astrojs/starlight/components';

This guide is for AI engineers building .NET applications that use LLMs and want full observability — traces, logs, metrics, evaluations, prompt versioning, and database correlation — through **OpenTelemetry** as the single source of telemetry. InfraAdvisor's .NET services are the worked example; the patterns transfer to any .NET LLM app.

If you're new to AI observability, the mental model in **§1** is the most important section. Skim the rest; return when you need to wire something up.

---

## 1. Why this exists

Production LLM applications fail in ways that classical observability (logs, traces, metrics) doesn't surface. A 200 OK on `/query` can return a hallucinated answer, a tool misuse, or a citation pointing to a non-existent document. Latency and error rate look fine; quality has collapsed silently.

Datadog LLM Observability (LLMObs) adds a quality layer on top of standard APM:

| Classical APM tells you | LLM Observability adds |
|---|---|
| Did the request succeed? | Was the answer correct? |
| How fast was it? | Did the agent use the right tool? |
| Did any service throw? | Did the citation match the source? |
| Token / cost totals | Per-trace cost broken out by model + tool |
| HTTP topology | Agent → LLM → Tool → Tool span hierarchy |

The path we take to get there is **100 % OpenTelemetry**: the OTel SDK is the only tracer in our pods, traces export over OTLP to a Datadog Agent collector, and the collector forwards to LLMObs. No Datadog .NET tracer runs alongside.

<Aside type="note">
**Why pure OpenTelemetry?** The Datadog .NET tracer doesn't classify gen_ai.\* spans as LLM Observability span kinds — that classification only happens on the OTLP ingest path. Running both tracers in parallel also splits trace IDs because each tracer extracts a different propagation header (DD reads `x-datadog-trace-id`, OTel reads W3C `traceparent`). Dropping the DD tracer is what unified our trace tree.
</Aside>

---

## 2. Mental model

Three things have to be true for DD LLMObs to render a useful trace:

1. **A span tree that follows OTel GenAI semantic conventions.** Operation names like `chat`, `invoke_agent`, `execute_tool`, `embeddings` are how DD classifies spans into LLMObs kinds (`llm`, `agent`, `tool`, `embedding`). DD auto-maps from `gen_ai.operation.name` — no manual mapping for the standard kinds.

2. **An `ml_app` resource attribute on every span.** DD groups traces, evals, prompts, datasets, and dashboards by `ml_app`. Without it, traces fall into the void. We set it in the in-cluster collector's `transform/llmobs` processor (mirrors `service.name`) so app code stays pure OTel.

3. **A `source=otel` resource attribute.** Tells DD this is OTel-instrumented. Without it, evaluations submitted via the external-evals API and OTel-emitted spans don't match up. Set in `TelemetrySetup.cs`.

The .NET stack we use:

```
Application code (pure OTel, no DD-tracer-specific calls)
  │
  ├─► Microsoft.Extensions.AI (M.E.AI)     ← emits chat + execute_tool + embeddings spans
  │     └─► Azure.AI.OpenAI                ← HttpClient auto-instrumented
  │
  ├─► Microsoft.Agents.AI (MAF)            ← emits invoke_agent spans
  │     └─► AgentSession (Redis JSON)
  │
  ├─► ModelContextProtocol(.AspNetCore)   ← emits MCP request spans + traceparent
  │                                          propagation across services
  ├─► Npgsql.OpenTelemetry                 ← db.system / db.statement attrs on
  │                                          every Postgres command
  └─► Custom ActivitySource                ← task, retrieval (manual spans)
        │
        ▼
   OTLP/HTTP → DatadogAgent OTel collector → DD LLM Observability + APM + DBM correlation
```

---

## 3. The trace anatomy

One `POST /query` produces eight distinct span kinds across two services, all under one trace_id:

```
[infra-advisor-ui]                                           SERVICE
RUM browser trace                                    (rum)   ← W3C traceparent injected
└── [agent-api-dotnet]
    POST /query                                      workflow ← AspNetCore
    ├── classify_domain                              task     ← manual ActivitySource
    ├── retrieve_best_practices                      retrieval ← manual
    │   └── embeddings                               embedding ← M.E.AI IEmbeddingGenerator
    └── invoke_agent                                 agent    ← MAF AIAgent
        └── chat                                     llm      ← M.E.AI IChatClient
            ├── HttpClient POST → Azure OpenAI       (http)
            └── execute_tool (e.g. get_bridge_*)     tool     ← M.E.AI UseFunctionInvocation
                └── HttpClient POST → mcp-server-dotnet (http)
                    └── [mcp-server-dotnet]          ↓ traceparent
                        POST /mcp/                   workflow ← AspNetCore
                          └── tools/call <name>      (mcp)    ← ModelContextProtocol SDK
                              └── HttpClient → upstream (http) ← FHWA / EPA / EIA / etc.
```

The **single trace_id** is the keystone. W3C `traceparent` propagates: browser RUM → agent → MCP server → upstream API. Click any span in DD LLMObs, see the full lineage. Click "View session replay" from the trace to see the user's actual click stream.

---

## 4. Feature catalog

Each feature below follows the same shape:
- **Goal** — the problem it solves
- **How we wired it** — code pointers
- **Outcome** — what you see in DD
- **Extending it** — recipe for adding more

### 4.A — Span kinds (workflow / agent / llm / tool / task / embedding / retrieval)

**Goal:** show the full agent decision tree, including non-LLM steps (classification, retrieval). Anything you'd want to graph latency / cost / failure rate by should be its own span kind.

| Kind | Source | Code |
|---|---|---|
| `workflow` | AspNetCore auto-instrumentation | `TelemetrySetup.cs: AddAspNetCoreInstrumentation()` |
| `agent` | MAF `.UseOpenTelemetry()` on agent builder | `Program.cs` AIAgent registration |
| `llm` | M.E.AI `.UseOpenTelemetry()` on chat client | `Program.cs` IChatClient registration |
| `tool` | M.E.AI `.UseFunctionInvocation()` | same line as `llm` |
| `task` | Manual `ActivitySource.StartActivity` + `dd.llmobs.span.kind=task` | `AgentService.ClassifyDomainTraced` |
| `embedding` | M.E.AI `.UseOpenTelemetry()` on IEmbeddingGenerator | `Program.cs` IEmbeddingGenerator registration |
| `retrieval` | Manual span + `dd.llmobs.span.kind=retrieval` | `RetrievalService.RetrieveAsync` |

DD auto-maps `chat → llm`, `invoke_agent → agent`, `execute_tool → tool`, `embeddings → embedding`. `task` and `retrieval` aren't in the OTel semconv as gen_ai operation names — we explicitly tag `dd.llmobs.span.kind=...` to force the classification.

**Extending it** — adding a new kind:

```csharp
// 1. Start from the same ActivitySource TelemetrySetup AddSource's
using var activity = ActivitySource.StartActivity("my_step", ActivityKind.Internal);

// 2. Tag the kind + a standard operation name for context
activity?.SetTag("dd.llmobs.span.kind", "task");      // or retrieval, etc.
activity?.SetTag("gen_ai.operation.name", "my_step");

// 3. Add input/output for the LLMObs UI to render
activity?.SetTag("input.value", queryOrInput);
// ... do the work ...
activity?.SetTag("output.value", result);
```

### 4.B — Prompt tracking

**Goal:** version the system prompt so changes are traceable in production. When prompt v2 ships, you want to compare latency / cost / quality eval scores between v1 and v2 by version tag without rebuilding dashboards.

**How we wired it:** `Program.cs` computes a content-derived version (`v1-<sha256[:8]>`) and registers a global `ActivityListener` that stamps `_dd.ml_obs.prompt_tracking` JSON metadata on every `invoke_agent` and `chat` span:

```csharp
var promptVersion = "v1-" + ShortContentHash(AgentSystemPrompt);
var promptTrackingJson = JsonSerializer.Serialize(new {
    name = "infra-advisor-system",
    version = promptVersion,
    template = AgentSystemPrompt,
    variables = new Dictionary<string, object>(),
});

ActivitySource.AddActivityListener(new ActivityListener { /* stamps spans */ });
```

**Outcome:** DD UI → LLM Observability → Prompts shows `infra-advisor-system v1-<hash>` with version diff capability. Once a v2 prompt ships, comparison charts (calls, latency, tokens, cost per version) work without further code.

### 4.C — External evaluations API

**Goal:** score the agent's output against rules that don't fit DD's managed evaluators — typically domain-specific tool use, output format, citation presence.

**How we wired it:**

1. **`IResponseEvaluator`** (`Services/Evaluators/`) — returns `EvalResult(metricType, value, reasoning)` for one agent response. Two shipped today:
   - `CitationPresentEvaluator` — boolean; regex against domain identifiers (NBI / PWSID / award_id).
   - `BdToolOrderingEvaluator` — boolean; asserts `get_contract_awards` precedes `get_procurement_opportunities`.

2. **`DatadogEvalsClient`** — typed `HttpClient` wrapping DD's `POST /api/intake/llm-obs/v2/eval-metric`. Tags `source:otel`, addresses the agent span by trace_id + span_id captured in `AgentSpanContext`.

`AgentService.RunAgentAsync` rolls `Random.Shared.NextDouble() < EVAL_SAMPLE_RATE` (default 0.1); if hit, fires `Task.Run` background eval that walks every `IResponseEvaluator` and POSTs. Fire-and-forget so `/query` latency is unchanged.

**Extending it** — adding a deterministic evaluator:

```csharp
public class MyEvaluator : IResponseEvaluator
{
    public string Label => "my_check";
    public EvalResult Evaluate(EvalInput input) =>
        new EvalResult("boolean", someBoolean, reasoning: "why");
}
```

Then `builder.Services.AddSingleton<IResponseEvaluator, MyEvaluator>()` in `Program.cs`. DI injects all `IResponseEvaluator` implementations into `AgentService` as `IEnumerable<>`. No further wiring.

### 4.D — Annotation queues (human review)

**Goal:** manually score real production traces. Annotation queues feed (eventually) the Datasets feature for offline evaluation.

**How we wired it:** UI-only — no code changes. Setup steps:

1. DD UI → LLM Observability → Annotations → Create queue.
2. Filter: `@ml_app:infra-advisor-agent-api-dotnet @meta.span.kind:agent` (full agent turns, not individual sub-spans).
3. Sample rate: 100 % while learning the schema; drop to 10–20 % once volume picks up.

### 4.E — Business metrics

**Goal:** track AI effectiveness alongside infrastructure metrics — tool usage frequency, conversation completion rate, feedback ratio.

| Counter | Tagged with | Emitted from |
|---|---|---|
| `infra_advisor.conversation.completed` | `query.domain` | `AgentService.RunAgentAsync` |
| `infra_advisor.tool.invoked` | `tool.name`, `query.domain` | per `FunctionCallContent` |
| `infra_advisor.feedback.submitted` | `rating` | `/feedback` endpoint |

All counters use `IMeterFactory.Create(TelemetrySetup.ActivitySourceName)` — same meter the OTel pipeline already exports, no extra wiring.

### 4.F — Managed evaluations (DD-built, UI-enabled)

**Goal:** get out-of-the-box quality signals (language mismatch, sensitive data, sentiment, etc.) without writing evaluator code.

UI setup only:
1. DD UI → LLM Observability → Settings → Evaluations.
2. Pick `ml_app:infra-advisor-agent-api-dotnet`.
3. Toggle evaluators (Language Mismatch, Sensitive Data Scanning confirmed; check your tenant for the full catalog).

### 4.G — Datasets + experiments (deferred)

**Goal:** offline regression testing.

**Status:** not implemented. DD's Experiments SDK is Python-only, and the strongest datasets come from real production traces. Recommended sequence:
1. Run the annotation queue 2–3 weeks until ~50 traces are scored.
2. Promote 30 of those into a DD-managed dataset.
3. Write a Python experiment runner — the task function POSTs to `/query`; evaluator logic ported from C#.
4. CI gate on every PR touching `Program.cs` system prompt.

### 4.H — Frustration detection (composable)

**Goal:** identify users having a bad conversation before they churn.

Compose from signals already in production:
- Sentiment managed eval (§4.F).
- Conversation grouping by `gen_ai.conversation.id` (MAF `AgentSession` emits this automatically).
- Feedback events (`infra_advisor.feedback.submitted{rating:negative}`).

Build a DD monitor on the combination:
```
avg(sentiment_score) by conversation.id < 0.3 over last 5 turns
  AND count(feedback.rating:negative) by conversation.id >= 1
```

---

## 5. DBM ↔ APM correlation (attribute-based, no SQL-comment injection)

**Goal:** when a query span is slow in the APM trace tree, jump straight to the matching pg_stat_activity sample in Database Monitoring.

The Datadog .NET tracer accomplishes this via SQL-comment injection (`DD_DBM_PROPAGATION_MODE=full`). With OTel only, we use **attribute-based correlation** instead — same outcome, no SQL rewriting.

**How we wired it:**

1. **Npgsql.OpenTelemetry** auto-emits the required attributes on every command span: `db.system`, `db.statement`, `db.name`, `db.user`, `peer.hostname`. App code only calls `.AddNpgsql()` in `TelemetrySetup.cs`.

2. **DD agent OTel collector processor** tags Npgsql spans with `span.type=sql`. DD's DBM ingest correlates spans-to-samples by statement match + timing window when this tag is present.

   ```yaml
   # datadog/datadog-agent.yaml (otelCollector config)
   processors:
     attributes/dbm:
       include:
         match_type: regexp
         attributes:
           - key: db.system
             value: ".+"          # matches "postgres" and "postgresql"
       actions:
         - key: span.type
           value: sql
           action: insert
   ```

3. **The Postgres pod** runs the standard DD postgres integration via autodiscovery annotation, with `dbm:true` and `reported_hostname` set so the host renders as a stable name in the DBM UI:

   ```yaml
   ad.datadoghq.com/postgres.checks: |
     {"postgres":{"instances":[{
       "dbm": true,
       "reported_hostname": "infra-advisor-postgres",
       "collect_schemas":         {"enabled": true},
       "collect_settings":        {"enabled": true},
       "collect_column_statistics": {"enabled": true},
       "collect_function_metrics":   true,
       "collect_activity_metrics":   true,
       "collect_wal_metrics":        true,
       "collect_bloat_metrics":      true,
       "collect_buffercache_metrics":true,
       "relations": [{"relation_regex": ".*"}]
     }]}}
   ```

**Reference:** [Datadog OTel DBM correlation](https://docs.datadoghq.com/opentelemetry/correlate/dbm_and_traces/).

---

## 6. Log → trace correlation

OTel SDK + Serilog gives you log ↔ trace linkage automatically when:

1. Serilog enriches logs from the `LogContext` (the request's ambient Activity).
2. Logs emit in JSON with the OTel trace/span ID fields (`@tr`, `@sp`).
3. The DD agent's container log collector recognizes the `csharp` source and parses those fields.

```csharp
// TelemetrySetup.cs
builder.Host.UseSerilog((ctx, services, lc) => lc
    .Enrich.FromLogContext()
    .Enrich.WithProperty("service.name", "infra-advisor-agent-api-dotnet")
    .WriteTo.Console(new RenderedCompactJsonFormatter()));
```

```yaml
# deployment.yaml annotation
ad.datadoghq.com/agent-api-dotnet.logs: '[{"source":"csharp","service":"infra-advisor-agent-api-dotnet"}]'
```

That's it. Click any APM trace → Logs tab at the bottom shows the matching structured log lines. No `DD_LOGS_INJECTION`, no enricher, no DD .NET tracer.

---

## 7. Common tasks (recipes)

### Add a new evaluator
1. Implement `IResponseEvaluator` in `Services/Evaluators/`.
2. `builder.Services.AddSingleton<IResponseEvaluator, MyEvaluator>()` in `Program.cs`.
3. Done — DI picks it up automatically.

### Add a new business counter
See §4.E "Extending it."

### Add OTel tracing to a new .NET service
1. Add NuGet packages: `OpenTelemetry.Extensions.Hosting`, `OpenTelemetry.Instrumentation.AspNetCore`, `OpenTelemetry.Instrumentation.Http`, `OpenTelemetry.Exporter.OpenTelemetryProtocol`.
2. Copy the `TelemetrySetup.cs` pattern from `agent-api-dotnet` — change `ActivitySourceName` + `serviceName`.
3. Configure env vars: `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_SERVICE_NAME`, `DD_ENV`, `DD_VERSION`.
4. In `Program.cs`: `TelemetrySetup.Configure(builder)` before `builder.Build()`.

### A/B test a system prompt
Define a second prompt constant, hash it for the version tag, pick one per request (random or sticky per user-ID hash), stamp the chosen version's JSON on the activity. The Prompt Tracking foundation in §4.B handles the rest.

### Look at all spans for one conversation
DD trace explorer: `@ml_app:infra-advisor-agent-api-dotnet @meta.gen_ai.conversation.id:<id>`. Group by `trace_id`.

### Debug why an evaluator isn't running
1. Check `EVAL_SAMPLE_RATE` env — must be > 0.
2. Confirm `DD_API_KEY` secret mounted (DatadogEvalsClient disables silently when missing — logs a WARN at startup).
3. Pod logs for "DD eval submission failed" warnings.
4. Confirm `AgentSpanContext.Current` populated — the ActivityListener in `Program.cs` should fire on every `invoke_agent` span.

---

## 8. Troubleshooting (the issues we hit)

### `source:undefined` on spans
Resource attribute missing. Fix: `TelemetrySetup.cs` adds `["source"] = "otel"` to the resource. Required because DD's external-evals API expects `source:otel` on the eval payload — keep both layers consistent.

### Trace tree split across two trace IDs (dual-tracer)
Our biggest historical bug. Symptom: `invoke_agent` lived on one trace_id while `classify_domain` / `retrieve_best_practices` / `embeddings` lived on a different one — same logical request, two halves in the UI.

**Cause:** the Datadog .NET tracer was admission-injected (`admission.datadoghq.com/dotnet-lib.version: v3`) and ran in parallel with the OTel SDK. DD tracer extracted `x-datadog-trace-id` from RUM's request headers; OTel SDK ignored that header and generated its own W3C trace_id. They never agreed.

**Fix:** removed the admission annotation entirely. OTel SDK reads RUM's W3C `traceparent` natively — single trace_id end-to-end. What we lose without the DD tracer: Code Origin for Spans, ASM/AAP RASP, IAST. For a pure-OTel LLM demo, that's the right trade.

### Postgres host shows as a pod IP in DBM UI
Without `reported_hostname` in the autodiscovery check config, the Postgres host renders as the pod's ephemeral IP. Set `"reported_hostname": "infra-advisor-postgres"` in the `ad.datadoghq.com/postgres.checks` annotation — DBM UI shows the stable name.

### MCP server missing from trace tree
The MCP server emitted zero trace spans because its `TelemetrySetup` only configured `.WithMetrics(...)` — no `.WithTracing` block. Fix:
- Bump `ModelContextProtocol.AspNetCore` to 1.3+ (first stable; ships the `Experimental.ModelContextProtocol` ActivitySource + traceparent propagation through MCP message metadata).
- Add `.WithTracing(...)` with `AddAspNetCoreInstrumentation`, `AddHttpClientInstrumentation`, `AddSource("Experimental.ModelContextProtocol")`.
- On the client side, `AddSource("Experimental.ModelContextProtocol")` too — captures the CLIENT-side MCP spans that bridge `execute_tool` to the server's request span.

### MCP tool calls return HTTP 400 / 404 across replicas
MCP 1.3.0's HTTP transport is session-stateful by default — each request after `initialize` carries an `Mcp-Session-Id` bound to one server pod. When the K8s Service round-robins to a different replica, follow-up requests fail. Two options:
- **`sessionAffinity: ClientIP`** on the Service (what we ship) — pins each client to one backend for the session lifetime.
- **`Stateless=true`** on `WithHttpTransport(options => …)` — server-side. Caveat: the .NET MCP **client** library still issues session-aware requests, so a stateless server returns 400 on every tool call. Use affinity instead.

### Evals not appearing on traces
`EVAL_SAMPLE_RATE` defaults to 0.1 — only 10 % of queries are scored. To verify the path works, temporarily set it to 1.0 in the configmap, redeploy, run one query, look for eval rows on that trace. Also confirm `DD_API_KEY` is set; without it `DatadogEvalsClient` silently no-ops.

### Image pull stuck after restart
`ImagePullBackOff` on a new replica usually means the `ghcr-pull-secret` Token has expired. Generate a fresh GitHub PAT with `read:packages`, `make create-ghcr-secret`, then `kubectl rollout restart`.

---

## 9. References

- DD OTel instrumentation: https://docs.datadoghq.com/llm_observability/setup/sdk/opentelemetry
- DD OTel DBM correlation: https://docs.datadoghq.com/opentelemetry/correlate/dbm_and_traces/
- DD Evaluations API: https://docs.datadoghq.com/llm_observability/instrumentation/api/?tab=model#evaluations-api
- DD Prompt Tracking: https://docs.datadoghq.com/llm_observability/monitoring/prompt_tracking/
- DD log-trace correlation (OTel): https://docs.datadoghq.com/tracing/other_telemetry/connect_logs_and_traces/opentelemetry/
- OTel GenAI semconv: https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-spans/
- Microsoft Agents Framework Evaluation: https://learn.microsoft.com/en-us/agent-framework/agents/evaluation?pivots=programming-language-csharp
- M.E.AI on NuGet: https://www.nuget.org/packages/Microsoft.Extensions.AI
- ModelContextProtocol C# SDK: https://github.com/modelcontextprotocol/csharp-sdk

---

<Aside type="note">
**Where this guide stops:** Wave 2 changes (React 18→19, Azure.Search.Documents 11→12, Npgsql 9→10, full LLM-judge evaluator suite, Python Experiments runner) aren't yet in production. When those land, this page is the right place to add their goal / wire-up / outcome sections.
</Aside>
