---
title: AI Engineering Guide — LLM Observability
description: A conceptual how-to for the DD LLM Observability feature surface InfraAdvisor uses — what each feature solves, how we wired it, and how to extend it.
---

import { Aside } from '@astrojs/starlight/components';

This guide is for AI engineers joining the InfraAdvisor project. It maps every Datadog LLM Observability feature in production to the code that emits it, the problem it solves, and the recipe for adding more.

If you're new to AI engineering: the mental model in **§1 Why This Exists** is the most important section. Skim everything else; come back when you need to wire something up.

---

## 1. Why this exists

Production LLM applications fail in ways that classical observability (logs, traces, metrics) doesn't surface. A 200 OK on `/query` can return a hallucinated answer, a tool-misuse, or a citation pointing to a non-existent document. Latency and error rate look fine; quality has collapsed silently.

Datadog LLM Observability adds a quality layer on top of standard APM:

| Classical APM tells you | LLM Observability adds |
|---|---|
| Did the request succeed? | Was the answer correct? |
| How fast was it? | Did the agent use the right tool? |
| Did any service throw? | Did the citation match the source? |
| Token / cost totals | Per-trace cost broken out by model + tool |
| HTTP topology | Agent → LLM → Tool → Tool span hierarchy |

Everything below is built around emitting OTel data that DD's LLMObs ingest understands, then layering evaluations + business metrics on top.

---

## 2. Mental model

Three things have to be true for DD LLMObs to render a useful trace:

1. **A span tree that follows OTel GenAI semantic conventions.** Operation names like `chat`, `invoke_agent`, `execute_tool` are how DD classifies spans into LLM Observability kinds (`llm`, `agent`, `tool`). DD auto-maps from `gen_ai.operation.name` — no manual mapping needed for the standard kinds.

2. **An `ml_app` resource attribute on every span.** DD groups traces, evals, prompts, datasets, and dashboards by `ml_app`. Without it, your traces fall into the void. We set it in the in-cluster collector's `transform/llmobs` processor (mirror of `service.name`) so app code stays pure OTel — see `datadog/datadog-agent.yaml`.

3. **`source=otel` resource attribute.** Tells DD this is OTel-instrumented (not DD-SDK-instrumented). Without it, evaluations submitted via the external-evals API and OTel-emitted spans don't match up. Set in `TelemetrySetup.cs` on both .NET services.

The .NET stack we use:

```
Application code (pure OTel, no DD-specific calls)
  │
  ├─► Microsoft.Extensions.AI (M.E.AI)   ← emits chat + execute_tool spans
  │     └─► Azure.AI.OpenAI               ← HttpClient auto-instrumented
  │
  ├─► Microsoft.Agents.AI (MAF)          ← emits invoke_agent spans
  │     └─► AgentSession (Redis)
  │
  ├─► ModelContextProtocol(.AspNetCore)  ← emits MCP req/exec spans + traceparent propagation
  │
  └─► Custom ActivitySource              ← task, retrieval (manual)
        │
        ▼
   OTLP/HTTP → DatadogAgent OTel collector → DD LLM Observability + APM
```

---

## 3. The trace anatomy

One `POST /query` produces eight distinct span kinds across two services:

```
[agent-api-dotnet]                                           SERVICE
POST /query                                          workflow ← AspNetCore
├── classify_domain                                  task     ← manual ActivitySource
├── retrieve_best_practices                          retrieval ← manual
│   └── embeddings                                   embedding ← M.E.AI IEmbeddingGenerator
└── invoke_agent                                     agent    ← MAF AIAgent
    └── chat                                         llm      ← M.E.AI IChatClient
        ├── HttpClient POST → Azure OpenAI           (http)
        └── execute_tool (e.g. get_bridge_condition) tool     ← M.E.AI UseFunctionInvocation
            └── HttpClient POST → mcp-server-dotnet  (http)
[mcp-server-dotnet]                                          ↓ traceparent
                POST /mcp                            workflow ← AspNetCore
                  └── MCP request/dispatch span      (mcp)    ← ModelContextProtocol SDK
                      └── HttpClient → upstream      (http)   ← FHWA / EPA / EIA / etc.
```

<Aside type="tip">
**One trace_id end-to-end.** The `ModelContextProtocol` SDK propagates the W3C `traceparent` header through MCP messages automatically — that's why both services share a trace. The fix that enabled this is documented in **§F Distributed Tracing** below.
</Aside>

---

## 4. Feature catalog

Each feature below follows the same shape:
- **Goal** — the problem it solves
- **How we wired it** — code pointers (not full snippets)
- **Outcome** — what you actually see in DD
- **Extending it** — recipe for adding more

### 4.A — Span kinds (workflow / agent / llm / tool / task / embedding / retrieval)

**Goal:** Show the full agent decision tree, including non-LLM steps (classification, retrieval). Anything you'd want to graph latency / cost / failure rate by should be its own span kind.

**How we wired it:**

| Kind | Source | Code |
|---|---|---|
| `workflow` | AspNetCore auto-instrumentation | `TelemetrySetup.cs: AddAspNetCoreInstrumentation()` |
| `agent` | MAF `.UseOpenTelemetry()` on agent builder | `Program.cs: builder.Services.AddSingleton<AIAgent>(...)` |
| `llm` | M.E.AI `.UseOpenTelemetry()` on chat client | `Program.cs: builder.Services.AddSingleton<IChatClient>(...)` |
| `tool` | M.E.AI `.UseFunctionInvocation()` (inside chat client) | same line as `llm` |
| `task` | Manual `ActivitySource.StartActivity` + `dd.llmobs.span.kind=task` | `AgentService.ClassifyDomainTraced` |
| `embedding` | M.E.AI `.UseOpenTelemetry()` on IEmbeddingGenerator | `Program.cs: builder.Services.AddSingleton<IEmbeddingGenerator<...>>(...)` |
| `retrieval` | Manual + `dd.llmobs.span.kind=retrieval` | `RetrievalService.RetrieveAsync` |

DD auto-maps `chat → llm`, `invoke_agent → agent`, `execute_tool → tool`, `embeddings → embedding`. `task` and `retrieval` aren't in the OTel semconv as gen_ai operation names — so we explicitly tag `dd.llmobs.span.kind=...` to force the classification.

**Outcome:** In DD LLM Observability → Traces, one `/query` renders all six kinds nested under the workflow root. Each kind gets its own latency / count / error charts in dashboards. Filter by `@meta.span.kind:tool` to see only tool calls.

**Extending it** — adding a new kind:

```csharp
// 1. Start the activity from the same ActivitySource TelemetrySetup AddSource's
using var activity = ActivitySource.StartActivity("my_step", ActivityKind.Internal);

// 2. Tag the kind + a standard operation name for context
activity?.SetTag("dd.llmobs.span.kind", "task");      // or retrieval, etc.
activity?.SetTag("gen_ai.operation.name", "my_step");

// 3. Add input/output for the LLMObs UI to render
activity?.SetTag("input.value", queryOrInput);
// ... do the work ...
activity?.SetTag("output.value", result);
```

The pattern is documented in `RetrievalService.cs` and `AgentService.ClassifyDomainTraced`.

### 4.B — Prompt tracking

**Goal:** Version the system prompt so changes are traceable in production. When prompt v2 ships, you want to compare latency / cost / quality eval scores between v1 and v2 by version tag without rebuilding dashboards.

**How we wired it:**

`Program.cs` computes a content-derived version (`v1-<sha256[:8]>`) and registers a global `ActivityListener` that stamps `_dd.ml_obs.prompt_tracking` JSON metadata on every `invoke_agent` and `chat` span. Same listener also captures the agent span's IDs into `AgentSpanContext` (used by the evals integration in §4.C).

```csharp
// Excerpt — Program.cs
var promptVersion = "v1-" + ShortContentHash(AgentSystemPrompt);
var promptTrackingJson = JsonSerializer.Serialize(new {
    name = "infra-advisor-system",
    version = promptVersion,
    template = AgentSystemPrompt,
    variables = new Dictionary<string, object>(),
});
ActivitySource.AddActivityListener(new ActivityListener { /* tags spans */ });
```

**Outcome:** DD UI → LLM Observability → Prompts shows `infra-advisor-system v1-<hash>` with side-by-side version diff capability. Once a v2 prompt ships, comparison charts (calls, latency, tokens, cost per version) work without further code.

**Extending it** — A/B prompt rotation:

1. Define a second prompt constant in `Program.cs`.
2. Compute its version hash.
3. In the request path, pick one randomly (or by user ID hash for stable cohorts).
4. Stamp the chosen version's JSON on the spans (override the listener's default).
5. Filter / split DD widgets by `_dd.ml_obs.prompt_tracking.version`.

### 4.C — External evaluations API

**Goal:** Score the agent's output against rules that don't fit DD's managed evaluators — typically domain-specific tool use, output format, citation presence.

**How we wired it:**

Two interfaces working together:

1. **`IResponseEvaluator`** (`Services/Evaluators/`) — returns `EvalResult(metricType, value, reasoning)` for one agent response. Today we ship two:
   - `CitationPresentEvaluator` — boolean, regex matches against domain identifiers (NBI / PWSID / award_id).
   - `BdToolOrderingEvaluator` — boolean, asserts `get_contract_awards` precedes `get_procurement_opportunities` when both called.

2. **`DatadogEvalsClient`** (`Services/DatadogEvalsClient.cs`) — typed `HttpClient` wrapping DD's `POST /api/intake/llm-obs/v2/eval-metric`. Always tags `source:otel`, addresses the agent span by trace_id + span_id captured in `AgentSpanContext`.

`AgentService.RunAgentAsync` rolls a `Random.Shared.NextDouble() < EVAL_SAMPLE_RATE` (default 0.1 = 10%); if hit, fires `Task.Run` background eval that walks every `IResponseEvaluator` and POSTs. Fire-and-forget so `/query` latency is unchanged.

**Outcome:** DD UI → LLM Observability → trace detail → Evaluations tab shows per-trace eval rows. The tags `query.domain:*` + `prompt.version:*` make these filterable in dashboards.

**Extending it** — adding a deterministic evaluator:

```csharp
// Services/Evaluators/MyEvaluator.cs
public class MyEvaluator : IResponseEvaluator
{
    public string Label => "my_check";              // snake_case → DD eval label
    public EvalResult Evaluate(EvalInput input) =>
        // input.Query, input.Answer, input.ToolsCalled, input.Sources, input.QueryDomain
        new EvalResult("boolean", someBoolean, reasoning: "why");
}
```

Then in `Program.cs`:
```csharp
builder.Services.AddSingleton<IResponseEvaluator, MyEvaluator>();
```

DI injects all `IResponseEvaluator` implementations into `AgentService` as an `IEnumerable<>`. No further wiring.

**Extending it** — adding an LLM-as-judge evaluator (future):

The interface is ready for `MetricType = "score"`. Use `Microsoft.Extensions.AI.Evaluation.Quality` evaluators (e.g. `GroundednessEvaluator`) inside the `Evaluate` body, reusing `IChatClient` we already have in DI. Returns a 0-1 score. DD's eval API accepts it natively.

### 4.D — Annotation queues (human review)

**Goal:** Manually score real production traces. Annotation queues are the data-collection layer that feeds (eventually) the Datasets feature for offline evaluation.

**How we wired it:** Pure UI feature — no code changes. Setup steps:

1. DD UI → LLM Observability → Annotations → Create queue.
2. Filter: `@ml_app:infra-advisor-agent-api-dotnet @meta.span.kind:agent` (full agent turns, not individual chat/tool sub-spans).
3. Sample rate: 100% while learning the schema; drop to 10-20% once volume picks up.

The recommended label schema (full table in `dd-annotations.md`):

| Label | Type | Required | Assessment | Why |
|---|---|---|---|---|
| `answer_quality` | Score 1-5 | Yes | Pass 3-5 | Headline metric |
| `failure_mode` | Categorical multi | No | No | Selecting any value = failure |
| `citation_validity` | Categorical | Yes | Pass valid + none_required | Track citation health |
| `risk_flagging` | Boolean | Yes | Pass false | Force conscious yes/no |
| `notes` | Free-text | No | N/A | Open commentary |

**Outcome:** A queue of human-scored traces. After 30+ annotations, "promote to dataset" workflow seeds the offline regression suite (§4.G).

### 4.E — Business metrics

**Goal:** Track AI effectiveness alongside infrastructure metrics — tool usage frequency, conversation completion rate, feedback ratio.

**How we wired it:** Three OTel counters on the existing meter pipeline (no new collector config needed):

| Counter | Tagged with | Emitted from |
|---|---|---|
| `infra_advisor.conversation.completed` | `query.domain` | `AgentService.RunAgentAsync` |
| `infra_advisor.tool.invoked` | `tool.name`, `query.domain` | `AgentService.RunAgentAsync` (per `FunctionCallContent`) |
| `infra_advisor.feedback.submitted` | `rating` | `Program.cs: /feedback` endpoint |

**Outcome:** DD Metrics explorer → `infra_advisor.*` shows time series per tag combination. Dashboards mix these with LLMObs eval metrics for "AI Effectiveness" widgets.

**Extending it** — a new counter:

```csharp
// In any class that takes IMeterFactory:
var meter = meterFactory.Create(TelemetrySetup.ActivitySourceName);
var counter = meter.CreateCounter<long>("infra_advisor.thing.happened",
    description: "What it tracks");

// Wherever the thing happens:
counter.Add(1,
    new KeyValuePair<string, object?>("dim1", value1),
    new KeyValuePair<string, object?>("dim2", value2));
```

The meter pipeline is set up in `TelemetrySetup.cs` and listens to any meter with the project's source name.

### 4.F — Managed evaluations (DD-built, UI-enabled)

**Goal:** Get out-of-the-box quality signals (language mismatch, sensitive data, sentiment, etc.) without writing evaluator code.

**How we wired it:** Nothing code-side. UI setup:

1. DD UI → LLM Observability → Settings → Evaluations.
2. Pick `ml_app:infra-advisor-agent-api-dotnet`.
3. Toggle evaluators. Confirmed available (verify the rest in your tenant):
   - Language Mismatch
   - Sensitive Data Scanning
4. DD samples ~10% of traces and runs the evaluator. Scores show on the trace detail's Evaluations tab alongside our external evals.

**Outcome:** Eval scores from DD's library appear on the same Evaluations tab as our custom ones — same UI, same filtering, no code.

**Extending it** — custom LLM-as-judge evaluators inside DD:
- Same UI, "Create LLM-as-judge" → write the judge prompt + pick output type (boolean / score / categorical).
- DD samples traces and runs the judge prompt on its own model.
- No code changes. Pays off when the judge prompt is stable enough to deploy widely.

### 4.G — Datasets + experiments (deferred)

**Goal:** Offline regression testing. Run the agent against a curated query set, score the outputs, compare runs side-by-side. The CI gate that blocks bad prompt changes.

**Status:** Not yet implemented. Two reasons:

1. **DD's Experiments SDK is Python-only.** Our agent is .NET. Pattern would be a separate Python script that POSTs to `/query` and uses `ddtrace`'s `LLMObs.experiment()` API for scoring. Doable but adds a runtime.
2. **Best datasets come from real production traces.** Building one before the annotation queue (§4.D) has ~30 reviewed traces gives you a synthetic dataset that tests imagined queries, not real ones.

**Recommended order:**
1. Run the annotation queue for 2-3 weeks until ~50 traces are scored.
2. Promote 30 of those (mix of domains, mix of pass/fail) into a DD-managed dataset.
3. Write the Python experiment runner — task function POSTs to `/query`, uses our same evaluator logic ported to Python.
4. Add CI gate that runs the experiment on every PR touching `Program.cs` system prompt.

### 4.H — Frustration detection (composable)

**Goal:** Identify users having a bad conversation before they churn.

**How we wired it:** Pure composition — no dedicated feature. The signals already in production:

1. **Sentiment managed eval** (toggle in §4.F).
2. **Conversation grouping** by `gen_ai.conversation.id` (already emitted automatically by MAF `AgentSession`).
3. **Feedback events** (`infra_advisor.feedback.submitted{rating:negative}` counter).

Build a DD monitor on the combination:

```
avg(sentiment_score) by conversation.id < 0.3 over last 5 turns
  AND count(feedback.rating:negative) by conversation.id >= 1
```

When it fires, the alert payload contains the conversation ID — operator pulls up the full trace tree in LLM Observability.

**Extending it:** Add domain-specific signals (e.g. specific failure_mode tags from annotation queues, repeat-query detection within a conversation) as additional `OR` clauses on the same monitor.

---

## 5. Common tasks (recipes)

### Add a new evaluator
1. Implement `IResponseEvaluator` in `Services/Evaluators/`.
2. `builder.Services.AddSingleton<IResponseEvaluator, MyEvaluator>()` in `Program.cs`.
3. Done — DI picks it up automatically.

### Add a new business counter
See §4.E "Extending it."

### Add OTel tracing to a new service
1. Add NuGet packages: `OpenTelemetry.Extensions.Hosting`, `OpenTelemetry.Instrumentation.AspNetCore`, `OpenTelemetry.Instrumentation.Http`, `OpenTelemetry.Exporter.OpenTelemetryProtocol`.
2. Copy the `TelemetrySetup.cs` pattern from `agent-api-dotnet` — change `ActivitySourceName` + `serviceName`.
3. Configure env vars: `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_SERVICE_NAME`, `DD_ENV`, `DD_VERSION`.
4. In `Program.cs`: `TelemetrySetup.Configure(builder)` before `builder.Build()`.

### A/B test a system prompt
See §4.B "Extending it."

### Look at all spans for one conversation
DD trace explorer: `@ml_app:infra-advisor-agent-api-dotnet @meta.gen_ai.conversation.id:<id>`. Group by `trace_id`.

### Debug why an evaluator isn't running
1. Check `EVAL_SAMPLE_RATE` env — must be > 0.
2. Confirm `DD_API_KEY` secret is mounted (DatadogEvalsClient disables itself silently when missing — logs a single WARN at startup).
3. Check pod logs for "DD eval submission failed" warnings.
4. Confirm `AgentSpanContext.Current` is populated — the ActivityListener in `Program.cs` should fire on every `invoke_agent` span.

---

## 6. Troubleshooting (the issues we hit)

### `source:undefined` on spans
Spans landed in DD without a `source` resource attribute. Fix: `TelemetrySetup.cs` adds `["source"] = "otel"` to the resource. Required because DD's external-evals API expects `source:otel` on the eval payload, and the resource attribute keeps it consistent on the span side.

### `mcp-server-dotnet` missing from trace tree
The MCP server emitted zero trace spans because its `TelemetrySetup.Configure` only called `.WithMetrics(...)` — no `.WithTracing` block. Fix:
- Bump `ModelContextProtocol.AspNetCore` 0.2.0-preview.3 → 1.3.0 (first stable; ships the `Experimental.ModelContextProtocol` ActivitySource).
- Add `.WithTracing(...)` with `AddAspNetCoreInstrumentation`, `AddHttpClientInstrumentation`, `AddSource("Experimental.ModelContextProtocol")`, `AddSource(custom)`.
- On the client side (agent-api-dotnet), `AddSource("Experimental.ModelContextProtocol")` too — captures the CLIENT-side MCP spans that bridge `execute_tool` to the server's incoming request span.

### Stale image after `git push`
CI auto-rolls via `kubectl rollout restart` in `build-push.yml`'s `deploy` job. If you see old `git.commit.sha` in span tags, check:
```bash
kubectl describe pod -n infra-advisor -l app=agent-api-dotnet | grep -E "Image:|Started:"
```
Image tags + start time tell you whether the rollout actually picked up the new image. CI's deploy job runs `kubectl rollout status --timeout=10m` so it should fail loudly if the rollout doesn't complete.

### Evals not appearing on traces
`EVAL_SAMPLE_RATE` defaults to 0.1 — only 10% of queries get scored. To verify the path works, temporarily set it to 1.0 in the configmap, redeploy, run one query, look for eval rows on that trace. Also confirm `DD_API_KEY` is set; without it, `DatadogEvalsClient` silently no-ops.

---

## 7. References

- DD LLM Observability Overview: https://docs.datadoghq.com/llm_observability/
- DD OTel instrumentation: https://docs.datadoghq.com/llm_observability/setup/sdk/opentelemetry
- DD Evaluations API: https://docs.datadoghq.com/llm_observability/instrumentation/api/?tab=model#evaluations-api
- DD Prompt Tracking: https://docs.datadoghq.com/llm_observability/monitoring/prompt_tracking/
- DD Experiments + Datasets: https://docs.datadoghq.com/llm_observability/experiments/setup/
- OTel GenAI semconv: https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-spans/
- Microsoft Agents Framework Evaluation: https://learn.microsoft.com/en-us/agent-framework/agents/evaluation?pivots=programming-language-csharp
- M.E.AI on NuGet: https://www.nuget.org/packages/Microsoft.Extensions.AI
- ModelContextProtocol C# SDK: https://github.com/modelcontextprotocol/csharp-sdk

---

<Aside type="note">
**Where this guide stops:** Wave 2 changes (React 18→19, Azure.Search.Documents 11→12, Npgsql 9→10, full LLM-judge evaluator suite, Python Experiments runner) aren't yet in production. When those land, this page is the right place to add their goal/wire-up/outcome sections.
</Aside>
