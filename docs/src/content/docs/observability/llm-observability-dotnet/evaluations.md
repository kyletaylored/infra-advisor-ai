---
title: Evaluations
description: External evaluations API (code-driven), annotation queues (human review), managed evals (DD-built), and the deferred datasets + experiments path.
sidebar:
  order: 3
  label: Evaluations
---

Four ways to score agent output, in increasing order of investment:

| Approach | Who scores | When to use |
|---|---|---|
| **Managed evals** | DD's built-in evaluators | Out-of-the-box quality signals (language, sensitive data, sentiment) — toggle in DD UI, no code. |
| **Annotation queues** | Humans | When you don't yet know what to measure — review real traces to learn the schema. |
| **External evals** | Code we ship | Domain-specific rules (tool order, citation presence, relevance, groundedness). |
| **Datasets + experiments** | Code, offline | Regression testing of prompt / model changes. CI-gateable. |

## External evaluations API

**Goal:** score the agent's output against rules that don't fit DD's managed evaluators — typically domain-specific tool use, output format, citation presence.

**How we wired it:**

1. **`IResponseEvaluator`** (`Services/Evaluators/`) — async; returns `Task<EvalResult>` so LLM-judge evaluators can await downstream model calls. Four shipped today:
   - `CitationPresentEvaluator` — deterministic boolean; regex against domain identifiers (NBI / PWSID / award_id).
   - `BdToolOrderingEvaluator` — deterministic boolean; asserts `get_contract_awards` precedes `get_procurement_opportunities`.
   - `MeaiRelevanceEvaluator` — LLM-judge score (1–5); wraps `Microsoft.Extensions.AI.Evaluation.Quality.RelevanceEvaluator`.
   - `MeaiGroundednessEvaluator` — LLM-judge score (1–5); wraps `GroundednessEvaluator` with tool-call outputs as grounding context.

2. **`DatadogEvalsClient`** — typed `HttpClient` wrapping DD's `POST /api/intake/llm-obs/v2/eval-metric`. Tags `source:otel`, addresses the agent span by trace_id + span_id captured in `AgentSpanContext`. Records every submission attempt to `EvalSubmissionLog` (50-entry ring buffer) for the admin diagnostics panel.

`AgentService.RunAgentAsync` rolls `Random.Shared.NextDouble() < EVAL_SAMPLE_RATE` (default 0.1); if hit, fires `Task.Run` background eval that walks every `IResponseEvaluator` and POSTs. Fire-and-forget so `/query` latency is unchanged. `EvalInput` includes `ToolResults` (capped at 4 KB per call) so judge-style evaluators have the raw tool output to verify claims against.

**Extending it** — adding a deterministic evaluator:

```csharp
public class MyEvaluator : IResponseEvaluator
{
    public string Label => "my_check";
    public Task<EvalResult> EvaluateAsync(EvalInput input, CancellationToken ct) =>
        Task.FromResult(new EvalResult("boolean", someBoolean, reasoning: "why"));
}
```

Then `builder.Services.AddSingleton<IResponseEvaluator, MyEvaluator>()` in `Program.cs`. DI injects all `IResponseEvaluator` implementations into `AgentService` as `IEnumerable<>`. No further wiring.

**Diagnostics** — `GET /eval/status` returns the live pipeline snapshot: sample rate, registered evaluators (with `is_llm_judge` flag), DD submission config, judge deployment, and the last 50 submission outcomes with timestamps, durations, and reasoning excerpts. The admin UI's **Eval pipeline (read-only)** panel polls this endpoint every 10 s. Read-only by design — env-driven config (`EVAL_SAMPLE_RATE`, `DD_API_KEY`) means runtime mutation would diverge from pod-restart-time truth. The panel is the fastest way to confirm "is the pipeline alive?" without leaving the app, especially when DD is disabled and submissions would otherwise be invisible.

## Annotation queues (human review)

**Goal:** manually score real production traces. Annotation queues feed (eventually) the Datasets feature for offline evaluation.

**How we wired it:** UI-only — no code changes. Setup steps:

1. DD UI → LLM Observability → Annotations → Create queue.
2. Filter: `@ml_app:infra-advisor-agent-api-dotnet @meta.span.kind:agent` (full agent turns, not individual sub-spans).
3. Sample rate: 100 % while learning the schema; drop to 10–20 % once volume picks up.

## Managed evaluations (DD-built, UI-enabled)

**Goal:** get out-of-the-box quality signals (language mismatch, sensitive data, sentiment, etc.) without writing evaluator code.

UI setup only:
1. DD UI → LLM Observability → Settings → Evaluations.
2. Pick `ml_app:infra-advisor-agent-api-dotnet`.
3. Toggle evaluators (Language Mismatch, Sensitive Data Scanning confirmed; check your tenant for the full catalog).

## Datasets + experiments (deferred)

**Goal:** offline regression testing.

**Status:** not implemented. DD's Experiments SDK is Python-only, and the strongest datasets come from real production traces. Recommended sequence:
1. Run the annotation queue 2–3 weeks until ~50 traces are scored.
2. Promote 30 of those into a DD-managed dataset.
3. Write a Python experiment runner — the task function POSTs to `/query`; evaluator logic ported from C#.
4. CI gate on every PR touching `Program.cs` system prompt.

## References

- [DD Evaluations API](https://docs.datadoghq.com/llm_observability/instrumentation/api/?tab=model#evaluations-api)
- [Microsoft Agents Framework Evaluation](https://learn.microsoft.com/en-us/agent-framework/agents/evaluation?pivots=programming-language-csharp)
- [M.E.AI on NuGet](https://www.nuget.org/packages/Microsoft.Extensions.AI)
