---
title: Annotation queues
description: Human-in-the-loop scoring of real production traces. The slowest evaluator and the most valuable one — humans see what evaluators can't.
sidebar:
  order: 4
---

import { Aside } from '@astrojs/starlight/components';

When your managed and LLM-judge evaluators have already done their thing, what's left is the **hard** cases — the ones humans need to look at. Annotation queues are DD's UI for that: pick a filter, sample some traces, route them to reviewers, capture their scores. The output feeds straight into [Datasets](../../experiments/) once you've accumulated enough labeled examples.

## When to use them

Three scenarios that come up in practice:

1. **Calibrating a new LLM-judge.** Run human scoring + LLM-judge in parallel for ~50 traces; if they correlate, ship the judge to higher sample rates. If they don't, the judge's prompt needs work.
2. **Investigating bad reviews.** User flagged a conversation as "not helpful" — does the human reviewer agree? Often the LLM behaved correctly and the user's expectation was off.
3. **Building a regression dataset.** Reviewers tag the "this should always work" cases. Over weeks, you accumulate the seed dataset for offline experiments.

## Setup

1. **DD UI → LLM Observability → Annotations → Create queue.**
2. **Filter.** Choose what gets queued. We use:
   - `@ml_app:infra-advisor-agent-api-dotnet @meta.span.kind:agent` — full agent turns, not individual sub-spans.
   - Add `@evaluations.meai_groundedness.value:<3` to focus on suspect cases.
3. **Sample rate.** 100% while learning the schema; 10–20% once you know what you're labeling.
4. **Schema.** What questions reviewers answer. Start small:
   - `quality`: bad / acceptable / good
   - `tool_choice`: wrong / suboptimal / right
   - `notes`: free text
   You can extend later, but a thicker schema gets fewer responses.
5. **Reviewers.** DD's role system controls who can score. Reviewers see the trace, the span tree, the prompt + completion, and the schema form.

## Workflow that actually works

A few patterns from real reviewing:

- **Daily batch, not real-time.** Reviewers do 10–20 at a time, once a day. Streaming labels are exhausting and produce worse data than batched ones.
- **Two reviewers on the first 30.** Inter-rater agreement is the only way to know if your schema is well-defined. If two reviewers disagree on `tool_choice`, rewrite the schema before scaling.
- **Reviewer notes are gold.** The "notes" field captures patterns the schema doesn't. After 50 traces, re-read the notes — they often reveal a new dimension to add to the schema.

## Outputs

Annotation scores appear on the trace just like managed/external evals do, with the reviewer's user ID as the source:

```
@evaluations.quality.value:good
@evaluations.tool_choice.value:right
@evaluations.notes:"agent picked get_bridge_condition correctly but missed the FIPS code in the answer"
```

DD also computes inter-rater statistics if multiple reviewers scored the same trace.

<Aside type="tip">
**Don't over-engineer the queue.** A queue that takes more than 60 seconds per trace to review gets abandoned. Keep the schema tight, the filters specific, and the volume manageable. 20 traces a day reviewed well beats 200 reviewed poorly.
</Aside>

## What's next

- [Export API](../export-api/) — pulling scored traces into your own dataset pipeline.
- [Experiments](../../experiments/) — the dataset path that the queue eventually feeds.
