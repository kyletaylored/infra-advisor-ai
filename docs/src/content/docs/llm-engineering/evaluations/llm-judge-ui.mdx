---
title: LLM-as-Judge (Datadog UI)
description: Define a custom LLM-judge evaluator entirely in DD's UI — pick a model, write a prompt, pick the spans to score. No code.
sidebar:
  order: 2
  label: LLM-as-Judge (DD UI)
---

import { Aside } from '@astrojs/starlight/components';

DD-UI LLM-as-Judge is the right tool when:
- Managed evals don't cover what you need.
- The evaluation logic is **prompt-shaped** (give the LLM the question + answer + judging criteria, get a score back).
- You don't want a code deploy to change the judging prompt.

For everything else — anything **deterministic** (regex, format check, ordering), anything that needs to inspect tool outputs not in the span, anything where you'd rather own the model + the eval client — use [External evaluations](../external/) instead.

## What you can build

A handful of judging templates that work well from the UI:

| Use case | Prompt shape | Output |
|---|---|---|
| **Helpfulness** | "Rate 1–5 how useful this answer is for the question." | score 1–5 |
| **Brand voice** | "Does the answer match our voice guidelines (professional, no jargon)?" | boolean |
| **Hallucination** | "Compare answer to retrieved sources. Score 1–5 for groundedness." | score 1–5 |
| **Action quality** | "Did the assistant take a useful next step or end the conversation prematurely?" | categorical |
| **Tone match** | "Did the assistant match the user's emotional tone appropriately?" | categorical |

Each is just a different prompt against the same `(question, answer, context)` tuple DD pulls from the span.

## Setup

1. **DD → LLM Observability → Settings → Evaluations → Create LLM-as-Judge.**
2. **Pick the model.** DD provides a few options; pick the cheapest one that gives stable scores. Most use cases work fine with a `mini` tier.
3. **Write the prompt.** DD interpolates `{{input}}` (the question) and `{{output}}` (the answer) into your template. Some templates also support `{{context}}` if you've annotated retrieved sources on the span.
4. **Pick the metric type:** boolean / score / categorical. Match it to what you ask the model to return.
5. **Pick the spans to score.** Filter by `@ml_app` and span kind (`agent` for full-turn evaluation, `llm` for individual model calls).
6. **Set the sample rate.** 10–20% is the right default. Bump higher only when you're calibrating; lower it once you trust the score.

## When a UI evaluator is the right tool

The UI path wins when:
- You want non-engineers (PMs, ML ops) to own the eval prompt.
- The eval can be rewritten without redeploying the app.
- The judging only needs what's already on the span (question, answer, maybe context).

The code path ([External](../external/)) wins when:
- The eval needs to inspect raw tool outputs (DD only sees what you annotate onto the span).
- The eval needs to call out to a system DD doesn't have access to (internal API, database).
- You want versioning, code review, and rollout gates on the evaluator itself.

<Aside type="tip">
**Start with a UI evaluator, graduate to code if needed.** It's much faster to iterate the prompt in DD's UI than in CI. If the eval logic stabilizes and you start wanting tests around it, port to [External](../external/).
</Aside>

## Comparing UI evaluators

Once you have two UI evaluators scoring the same span (e.g., "helpfulness" and "hallucination"), DD's correlation view will surface relationships: "helpful but hallucinated" traces are usually the most damaging — confident wrong answers. Set a monitor on that intersection.

## What's next

- [External](../external/) — the code-driven path, plus our `IResponseEvaluator` plugin pattern.
- [Annotation queues](../annotation-queues/) — when neither managed nor LLM-judge is good enough.
