---
title: Managed evaluations
description: Datadog-built evaluators (language mismatch, sensitive data, sentiment, etc.) — toggle them on in the UI and they apply to your existing spans.
sidebar:
  order: 1
  label: Managed
---

import { Aside } from '@astrojs/starlight/components';

Managed evaluations are DD-built evaluators that run inside DD's pipeline against the spans you're already emitting. Zero code; UI setup only. They're the right starting point — turn on the relevant ones before you write a single custom evaluator.

## What's available

Toggleable in DD UI → LLM Observability → Settings → Evaluations. The exact catalog evolves; today's notable ones:

| Evaluator | What it scores | Output |
|---|---|---|
| **Language Mismatch** | Did the answer come back in a different language than the question? | boolean |
| **Sensitive Data Scanning** | Did the prompt or answer include PII / secrets? | boolean (+ types) |
| **Sentiment** | Tone of the user query | categorical (positive/neutral/negative) |
| **Topic Relevance** | Does the answer stay on-topic for the question? | score 1–5 |
| **Failure to Answer** | Did the agent refuse or punt? | boolean |
| **Prompt Injection** | Does the question look like an injection attempt? | boolean |

DD runs these on your existing span data after ingest — no extra spans, no extra cost per trace beyond DD's flat eval-pipeline charge.

## Setup

1. **DD → LLM Observability → Settings → Evaluations.**
2. Pick your `ml_app` (e.g., `infra-advisor-ai` for Python, `infra-advisor-agent-api-dotnet` for .NET).
3. Toggle the evaluators you want.
4. **Sampling:** evaluators default to 100% in DD's UI. Drop to 10–20% once volume picks up — they cost the same per evaluation as your own.

That's it. New traces start getting scored within a few minutes. The scores appear under the "Evaluations" tab on every LLMObs span.

## What you do with the scores

Three patterns worth setting up immediately after enabling managed evals:

**1. Save a "failure mode" view.** In the trace explorer:

```
@ml_app:infra-advisor-ai @evaluations.failure_to_answer.value:true
```

Filters to every trace where the agent refused. Sort by recency. Review the top few weekly.

**2. Alert on language mismatch.** Indicates a routing bug if your app is single-language:

```
sum(@evaluations.language_mismatch.value:true).rolling_count(1h) > 5
```

**3. Compose sentiment + feedback into a frustration signal.** See [Monitoring → Metrics → Frustration detection](../../monitoring/metrics/#frustration-detection-composable) for the monitor query.

<Aside type="tip">
**Don't replace managed evals with custom ones.** It's tempting to write your own "is the answer on-topic" evaluator. The managed Topic Relevance is already calibrated against a large corpus — your custom version starts at zero calibration. Write custom evaluators for **domain-specific** rules (tool order, citation format, internal policy) that managed evals can't see.
</Aside>

## Limits

- **Latency to score.** Managed evals run asynchronously after ingest. The score appears 30s–2min after the trace lands. Don't gate user-facing logic on managed eval results.
- **Coverage.** Only the spans DD classifies as `llm` or `agent` are eligible for managed eval; `tool` and `task` spans aren't scored.
- **Customization.** No knobs beyond on/off and sample rate. If you need a custom threshold or prompt for the evaluator, use [LLM-as-Judge (DD UI)](../llm-judge-ui/) instead.

## What's next

- [LLM-as-Judge (DD UI)](../llm-judge-ui/) — DD-UI-defined LLM-judge evaluators with custom prompts.
- [External](../external/) — code-driven evaluators when DD UI isn't enough.
- [Annotation queues](../annotation-queues/) — humans-in-the-loop when no evaluator is good enough.
