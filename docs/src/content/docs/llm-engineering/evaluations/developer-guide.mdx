---
title: Evaluator developer guide
description: Concrete recipes for shipping a new evaluator end-to-end — code, test, deploy, verify in DD UI. Python and .NET both.
sidebar:
  order: 6
---

import { Tabs, TabItem, Aside } from '@astrojs/starlight/components';

By the end of this page, you'll have a new custom evaluator scoring real production traces in DD LLMObs. We'll walk through one deterministic example and one LLM-judge example, in both languages.

## When to write a custom evaluator

A check goes here when it's:
- Not covered by [Managed evals](../managed/) (it's domain-specific).
- More complex than what [DD-UI LLM-judge](../llm-judge-ui/) can express (e.g., needs tool outputs, needs CI/CD-style review).
- Worth versioning in code (so a change goes through review, not a UI form).

## Recipe: deterministic citation check

The check: did the agent's answer include at least one valid NBI structure number (8-digit ID)?

<Tabs syncKey="lang">
  <TabItem label="Python (ddtrace)">

```python
# services/agent-api/src/observability/evaluators/citation.py
import re
from ddtrace.llmobs import LLMObs

NBI_PATTERN = re.compile(r"\b\d{8}\b")

def evaluate_citation_present(trace_id: str, span_id: str, query_domain: str, answer: str) -> None:
    has_citation = bool(NBI_PATTERN.search(answer))
    LLMObs.submit_evaluation(
        span_context={"trace_id": trace_id, "span_id": span_id},
        label="citation_present",
        metric_type="boolean",
        value=has_citation,
        tags={"query.domain": query_domain, "source": "otel"},
    )
```

Call it from wherever you finish handling a query, fire-and-forget:

```python
import asyncio
asyncio.create_task(evaluate_citation_present(trace_id, span_id, domain, answer))
```

  </TabItem>
  <TabItem label=".NET (OpenTelemetry)">

```csharp
// services/agent-api-dotnet/Services/Evaluators/CitationPresentEvaluator.cs
public class CitationPresentEvaluator : IResponseEvaluator
{
    private static readonly Regex NbiPattern = new(@"\b\d{8}\b", RegexOptions.Compiled);

    public string Label => "citation_present";

    public Task<EvalResult> EvaluateAsync(EvalInput input, CancellationToken ct)
    {
        var hasCitation = NbiPattern.IsMatch(input.Answer);
        return Task.FromResult(new EvalResult(
            MetricType: "boolean",
            Value: hasCitation,
            Reasoning: hasCitation ? "Found 8-digit ID" : "No NBI structure number in answer"));
    }
}
```

Register in DI:

```csharp
// services/agent-api-dotnet/Program.cs
builder.Services.AddSingleton<IResponseEvaluator, CitationPresentEvaluator>();
```

Done. DI injects all `IResponseEvaluator` registrations into `AgentService`, which runs them on a sampled subset of `/query` requests.

  </TabItem>
</Tabs>

## Recipe: LLM-judge for tool ordering

The check: in BD-domain queries, the agent should call `get_contract_awards` before `get_procurement_opportunities` to avoid double-counting. A judge model can reason about edge cases (e.g., when only one tool was needed) better than a hard rule.

<Tabs syncKey="lang">
  <TabItem label="Python (ddtrace)">

```python
# services/agent-api/src/observability/evaluators/tool_ordering.py
from openai import AsyncAzureOpenAI

_JUDGE_PROMPT = """You are evaluating tool-call ordering for a business-dev assistant.

Tools used (in order): {tools_called}
Question: {query}
Answer: {answer}

Score 1-5: Did the assistant call tools in an order that avoids double-counting awards?
Reply with ONLY a number 1-5."""

async def evaluate_tool_ordering(trace_id, span_id, query, answer, tools_called, domain):
    if domain != "business_development":
        return  # skip — not applicable

    client = AsyncAzureOpenAI(
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        api_version="2025-01-01-preview",
    )

    resp = await client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role": "user",
                   "content": _JUDGE_PROMPT.format(
                       tools_called=", ".join(tools_called),
                       query=query, answer=answer)}],
        temperature=0, max_tokens=5,
    )
    score = float(resp.choices[0].message.content.strip())
    LLMObs.submit_evaluation(
        span_context={"trace_id": trace_id, "span_id": span_id},
        label="tool_ordering",
        metric_type="score",
        value=score,
        tags={"query.domain": domain, "source": "otel"},
    )
```

  </TabItem>
  <TabItem label=".NET (OpenTelemetry)">

Wrap `Microsoft.Extensions.AI.Evaluation`'s judge framework. For a simple case, you can subclass the existing `MeaiQualityEvaluator` base — but for full control, implement `IResponseEvaluator` directly:

```csharp
// services/agent-api-dotnet/Services/Evaluators/ToolOrderingEvaluator.cs
public class ToolOrderingEvaluator : IResponseEvaluator
{
    private readonly IChatClient _judge;

    public ToolOrderingEvaluator(IChatClient judge) => _judge = judge;

    public string Label => "tool_ordering";

    public async Task<EvalResult> EvaluateAsync(EvalInput input, CancellationToken ct)
    {
        if (input.QueryDomain != "business_development")
            return new EvalResult("score", 0, reasoning: "skipped — not BD domain");

        var prompt = $"""
            You are evaluating tool-call ordering for a business-dev assistant.

            Tools used (in order): {string.Join(", ", input.ToolsCalled)}
            Question: {input.Query}
            Answer: {input.Answer}

            Score 1-5: Did the assistant call tools in an order that avoids double-counting awards?
            Reply with ONLY a number 1-5.
            """;

        var response = await _judge.GetResponseAsync(
            new[] { new ChatMessage(ChatRole.User, prompt) }, cancellationToken: ct);
        var score = double.TryParse(response.Message.Text?.Trim(), out var s) ? s : 0;

        return new EvalResult("score", score,
            reasoning: $"[{score}] judge model decision");
    }
}
```

Register + DI injects automatically.

  </TabItem>
</Tabs>

## Test before deploy

Both backends support unit-testing evaluators in isolation — mock the inputs, assert on the EvalResult.

<Tabs syncKey="lang">
  <TabItem label="Python (ddtrace)">

```python
# services/agent-api/tests/test_citation_eval.py
import pytest
from unittest.mock import patch
from observability.evaluators.citation import evaluate_citation_present

@pytest.mark.asyncio
async def test_finds_nbi():
    with patch("ddtrace.llmobs.LLMObs.submit_evaluation") as mock_submit:
        await evaluate_citation_present("tid", "sid", "engineering",
                                        "Bridge 12345678 is in fair condition.")
        mock_submit.assert_called_once()
        kwargs = mock_submit.call_args.kwargs
        assert kwargs["value"] is True
```

Run: `uv run pytest -x services/agent-api/tests/test_citation_eval.py`

  </TabItem>
  <TabItem label=".NET (OpenTelemetry)">

```csharp
// services/agent-api-dotnet/tests/CitationPresentEvaluatorTests.cs
[Fact]
public async Task FindsNbiStructureNumber()
{
    var ev = new CitationPresentEvaluator();
    var input = new EvalInput(
        Query: "show me bridge 12345678",
        Answer: "Bridge 12345678 is in fair condition.",
        ToolsCalled: new[] { "get_bridge_condition" },
        ToolResults: Array.Empty<string>(),
        Sources: Array.Empty<string>(),
        QueryDomain: "engineering");

    var result = await ev.EvaluateAsync(input, default);
    Assert.Equal("boolean", result.MetricType);
    Assert.Equal(true, result.Value);
}
```

Run: `dotnet test services/agent-api-dotnet/`

  </TabItem>
</Tabs>

## Verify in DD UI

After deploy:

1. Fire one query that should trigger your evaluator (set `EVAL_SAMPLE_RATE=1.0` temporarily to guarantee it runs).
2. **DD → LLM Observability → Traces** — find the trace.
3. Click the trace → **Evaluations** tab. Your label should appear with the score and reasoning.
4. (.NET only) Confirm the **Eval pipeline (read-only)** admin panel also shows the submission with `success: true`.

If the score doesn't show within ~30 s:

| Symptom | Likely cause |
|---|---|
| Admin panel shows submission with `success: false` | Check the error column. Common: HTTP 400 (bad payload), 403 (wrong key) |
| Admin panel shows submission with "DD_API_KEY not set" | Set the secret, restart pod |
| No submission at all in admin panel | Evaluator not registered in DI (check `Program.cs`) or sample roll didn't hit |
| Submission succeeded but eval missing in DD UI | Wait 60s — DD ingest is async; or `source:otel` tag missing from the eval payload |

## Cost & calibration

- **Deterministic evaluators are free** beyond CPU. Run at 100% if you want.
- **LLM-judge evaluators add one inference per scored trace.** At 10% sample × 2 judges = +2% inference cost across all traffic. Don't run two judges at 100%.
- **Calibrate against humans first.** Run an annotation queue + your LLM-judge in parallel for 50 traces. If they correlate >0.7, ship the judge. If not, iterate on the judge's prompt.

<Aside type="tip">
**Threshold scores cautiously.** A judge's "4 out of 5" usually means "good enough, no obvious issues." Don't set monitors at `score < 4` until you've verified that the judge actually distinguishes 3s from 4s consistently. Better to alert on **trends** ("avg score this week 0.5 below last week") than absolute thresholds.
</Aside>

## What's next

- [External evaluations](../external/) — the underlying API and plugin pattern this guide builds on.
- [Annotation queues](../annotation-queues/) — human scoring for calibration.
