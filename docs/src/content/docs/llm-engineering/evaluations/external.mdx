---
title: External evaluations
description: Code-driven evaluators that POST scores to DD via the external-evaluations API. Plus the IResponseEvaluator plugin pattern, M.E.AI Quality wrappers, and the read-only diagnostics panel.
sidebar:
  order: 3
  label: External
---

import { Tabs, TabItem, Aside } from '@astrojs/starlight/components';

External evaluations are the right tool when:
- The check is **deterministic** (regex, ordering, format).
- The check needs to inspect **tool outputs**, raw inputs, or other state DD doesn't see on the span.
- You want **code review and versioning** on the evaluator itself.

The pattern: after a trace completes, run a sampled background eval, score it, POST the score to DD's eval-metric API. DD joins the score to the existing OTel-emitted span by `trace_id` + `span_id`.

## The wire format

A single eval-metric POST looks like:

```http
POST https://api.<site>.datadoghq.com/api/intake/llm-obs/v2/eval-metric
DD-API-KEY: <key>
Content-Type: application/json

{
  "data": {
    "type": "evaluation_metric",
    "attributes": {
      "metrics": [{
        "join_on": { "span": { "trace_id": "<decimal>", "span_id": "<decimal>" } },
        "metric_type": "score",
        "ml_app": "infra-advisor-agent-api-dotnet",
        "timestamp_ms": 1715856000000,
        "label": "meai_relevance",
        "score_value": 4.5,
        "tags": ["source:otel"],
        "reasoning": "[4] Answer addresses the question with relevant specifics."
      }]
    }
  }
}
```

`source:otel` in the tags is required so DD joins to OTel-emitted spans correctly (mirroring the `source = otel` resource attribute on the span side).

## The plugin pattern

Both backends implement the same shape: a pluggable evaluator interface + a sampling dispatcher + a typed HTTP client.

<Tabs syncKey="lang">
  <TabItem label="Python (ddtrace)">

ddtrace's `LLMObs.submit_evaluation()` is the wire wrapper. We use it both for code-driven evals and for user feedback:

```python
# services/agent-api/src/observability/llm_obs.py (excerpt)
def submit_user_feedback(trace_id, span_id, rating, session_id=None):
    tags = {}
    if session_id:
        tags["session.id"] = session_id

    LLMObs.submit_evaluation(
        span_context={"trace_id": trace_id, "span_id": span_id},
        label="user_feedback",
        metric_type="categorical",
        value=rating,
        tags=tags,
    )
```

And the fire-and-forget faithfulness evaluator:

```python
async def _compute_faithfulness(query, context_chunks, answer, session_id, query_domain):
    eval_model = os.environ.get("AZURE_OPENAI_EVAL_DEPLOYMENT", "gpt-4.1-mini")
    context_text = "\n---\n".join(context_chunks[:5]) or "(no context)"
    user_content = f"Context:\n{context_text}\n\nQuestion: {query}\n\nAnswer: {answer}"

    # LLMObs.task() wraps the eval call (which itself produces an
    # auto-instrumented OpenAI child span). The eval score is annotated
    # onto the task span; DD's eval pipeline picks it up by tag.
    with LLMObs.task("faithfulness-eval") as eval_span:
        response = await client.chat.completions.create(
            model=eval_model,
            messages=[
                {"role": "system", "content": _EVAL_SYSTEM_PROMPT},
                {"role": "user",   "content": user_content},
            ],
            temperature=0,
            max_tokens=5,
        )
        score = max(0.0, min(1.0, float(response.choices[0].message.content.strip())))

        LLMObs.annotate(
            span=eval_span,
            tags={
                "session.id": session_id,
                "eval.faithfulness_score": str(score),
                "eval.model": eval_model,
            },
        )
```

Two takeaways: (1) the eval runs in a `task` span so it's visible in LLMObs as a separate sub-trace, (2) the inner OpenAI call is auto-instrumented as a separate `llm` span — no manual wrapping needed.

  </TabItem>
  <TabItem label=".NET (OpenTelemetry)">

We define an `IResponseEvaluator` interface and let DI inject all implementations as `IEnumerable<IResponseEvaluator>`:

```csharp
// services/agent-api-dotnet/Services/Evaluators/IResponseEvaluator.cs
public interface IResponseEvaluator
{
    string Label { get; }
    Task<EvalResult> EvaluateAsync(EvalInput input, CancellationToken ct);
}

public record EvalInput(
    string Query,
    string Answer,
    IReadOnlyList<string> ToolsCalled,
    IReadOnlyList<string> ToolResults,   // raw tool data, capped 4 KB each
    IReadOnlyList<string> Sources,
    string QueryDomain);

public record EvalResult(string MetricType, object Value, string? Reasoning);
```

Four ship today: two deterministic (`CitationPresent`, `BdToolOrdering`), two LLM-judge wrappers around Microsoft.Extensions.AI.Evaluation.Quality (`MeaiRelevance`, `MeaiGroundedness`). All four return through the same dispatcher:

```csharp
// services/agent-api-dotnet/Services/AgentService.cs (excerpt)
private void ScheduleEvaluations(string query, string answer, ...)
{
    if (Random.Shared.NextDouble() >= _evalSampleRate) return;  // sample first

    var input = new EvalInput(query, answer, toolsCalled, toolResults, sources, domain);
    var traceId = AgentSpanContext.Current?.TraceIdDecimal;
    var spanId  = AgentSpanContext.Current?.SpanIdDecimal;

    foreach (var ev in _evaluators)
    {
        // Fire-and-forget — /query latency is unaffected by eval pipeline.
        _ = Task.Run(async () =>
        {
            try
            {
                var result = await ev.EvaluateAsync(input, CancellationToken.None);
                await _ddClient.SubmitAsync(traceId, spanId, ev.Label, result);
            }
            catch (Exception ex)
            {
                _logger.LogWarning(ex, "Evaluator {Label} threw", ev.Label);
            }
        });
    }
}
```

And the M.E.AI Quality wrapper (LLM-judge):

```csharp
// services/agent-api-dotnet/Services/Evaluators/MeaiQualityEvaluator.cs (excerpt)
public abstract class MeaiQualityEvaluator : IResponseEvaluator
{
    private readonly IChatClient _judge;
    public abstract string Label { get; }
    protected abstract IEvaluator CreateEvaluator();
    protected virtual EvaluationContext? BuildContext(EvalInput input) => null;

    public async Task<EvalResult> EvaluateAsync(EvalInput input, CancellationToken ct)
    {
        var inner = CreateEvaluator();
        var config = new ChatConfiguration(_judge);
        var messages = new[]
        {
            new ChatMessage(ChatRole.User, input.Query),
            new ChatMessage(ChatRole.Assistant, input.Answer),
        };
        var ctx = BuildContext(input);
        var result = await inner.EvaluateAsync(messages, config, ctx, ct);

        var metric = result.Get<NumericMetric>(inner.EvaluationMetricNames.Single());
        return new EvalResult("score", metric.Value ?? 0,
            reasoning: $"[{metric.Value}] {metric.Reason}");
    }
}

public class MeaiRelevanceEvaluator : MeaiQualityEvaluator
{
    public override string Label => "meai_relevance";
    protected override IEvaluator CreateEvaluator() => new RelevanceEvaluator();
}

public class MeaiGroundednessEvaluator : MeaiQualityEvaluator
{
    public override string Label => "meai_groundedness";
    protected override IEvaluator CreateEvaluator() => new GroundednessEvaluator();
    protected override EvaluationContext? BuildContext(EvalInput input) =>
        input.ToolResults.Count > 0
            ? new GroundednessEvaluatorContext(string.Join("\n\n---\n\n", input.ToolResults))
            : null;
}
```

The shipped `DatadogEvalsClient` wraps the HTTP POST with retry, error logging, and ring-buffer recording for the diagnostics panel.

  </TabItem>
</Tabs>

## Sampling

External evals add inference cost (LLM-judge especially). We sample at 10% by default via `EVAL_SAMPLE_RATE`:

- `0.1` = 10% of traces get scored. At low traffic, set higher temporarily to verify the path works.
- Each LLM-judge evaluator adds one inference call per scored trace. With two LLM-judge evaluators + 10% sample rate, that's a +2% inference overhead across all traffic.

## Diagnostics

`.NET` ships a `GET /eval/status` endpoint and a read-only "Eval pipeline" admin panel that polls every 10 s. Shows current sample rate, registered evaluators (with `is_llm_judge` flag), DD config, and the last 50 submission outcomes. The fastest way to confirm "is the pipeline alive?" without leaving the app.

The panel records every submission attempt — including the `DD_API_KEY missing → skipped` case — so misconfigurations are visible. See [Recipes → Debug why an evaluator isn't running](../developer-guide/#debug-why-an-evaluator-isnt-running).

<Aside type="caution">
**Don't block /query on evals.** Both backends run evals as fire-and-forget background tasks. A flaky evaluator must never affect user-facing latency or success rate. If your eval pipeline starts blocking, redesign before scaling sample rate.
</Aside>

## What's next

- [Developer guide](../developer-guide/) — recipe for adding your own evaluator.
- [Annotation queues](../annotation-queues/) — human-in-the-loop scoring that feeds dataset construction.
- [Export API](../export-api/) — programmatic access to scored spans for offline analysis.
