---
title: Experiments
description: Offline regression testing for LLM apps — datasets, experiment runs, prompt optimization, the Playground. Currently aspirational for InfraAdvisor; here's the path forward.
sidebar:
  order: 4
---

import { Aside, CardGrid, LinkCard } from '@astrojs/starlight/components';

<Aside type="caution">
**Roadmap, not shipped.** InfraAdvisor doesn't currently run DD Experiments. This page sets the intent and the data plan so we can adopt it cleanly when we get there. Everything below describes how DD's feature works and how we'd integrate it.
</Aside>

DD Experiments is the offline-testing half of LLMObs. Where the rest of the guide is about **production observability** ("what's happening right now"), Experiments is about **regression testing** ("did my change make things worse"). It's the path from "I changed the prompt and ran a few traces" to "I can prove this change is safe before deploy."

## The DD Experiments primitives

DD organizes the workflow around four objects:

| Object | What it is |
|---|---|
| **Dataset** | A frozen set of input/expected-output pairs. Produced by curating annotation queue results, or by manual construction. |
| **Experiment Run** | A pass over a dataset using a specific app version + prompt. Each row in the dataset gets an LLM call, the answer is captured, evaluators run against it. |
| **Evaluator** | Same as production evaluators (managed, LLM-judge, or external). The eval runs against each row's output. |
| **Comparison** | Two experiment runs side-by-side, score deltas per row. Used to decide ship/no-ship. |

The unit of progress isn't a single run — it's the *delta between runs*. "v2 prompt scored 0.4 higher on average and 0 worse than v1 on any row" is what ships.

## How we'd integrate at InfraAdvisor

The sequence we'd follow:

1. **Run annotation queue 2–3 weeks** to accumulate ~50 labeled production traces.
2. **Promote ~30 of those into a DD-managed dataset.** Curate to cover the four domains (engineering, water_energy, business_development, document) and include edge cases.
3. **Write a Python experiment runner** that the dataset can drive:
   - Pulls one row at a time from the dataset.
   - Hits our `/query` endpoint with the input.
   - Captures the answer + intermediate trace.
   - Submits scores via the evaluators we already ship.
4. **CI gate on prompt changes.** Every PR that touches `Program.cs`'s system prompt (or `agent.py`'s) triggers an experiment run; merge blocks if the run scores below the baseline.
5. **Prompt Optimization (later).** DD's prompt-optimization feature suggests prompt rewrites that improve scores on the dataset. Useful once the dataset and evaluators are stable.

## Why this is deferred

Two practical reasons:

- **The dataset doesn't exist yet.** Real users haven't generated enough labeled traces to seed it. Synthetic datasets are tempting but tend to encode our assumptions rather than user reality.
- **The DD Experiments SDK is Python-only.** Our .NET backend would need to drive experiments via the HTTP API (which exists), or via a Python wrapper that POSTs into the .NET `/query`. Workable, but not free.

Neither blocker is permanent. When user volume crosses some threshold (probably ~500 traces a day) and we've run annotation reviews for a month, the path forward is clear.

## What DD Experiments offers when we get there

A quick map of what each sub-feature does, so the eventual onboarding goes faster:

<CardGrid>
  <LinkCard title="Setup and Usage" href="https://docs.datadoghq.com/llm_observability/experiments/" description="UI walkthrough — create dataset, attach evaluators, kick off a run." />
  <LinkCard title="Datasets" href="https://docs.datadoghq.com/llm_observability/experiments/datasets/" description="Schema, versioning, promotion from annotation queue, manual entry." />
  <LinkCard title="Analyzing Results" href="https://docs.datadoghq.com/llm_observability/experiments/analyzing_results/" description="Run-vs-run comparison views, per-row deltas, statistical significance." />
  <LinkCard title="Advanced Experiment Runs" href="https://docs.datadoghq.com/llm_observability/experiments/advanced/" description="Cross-product runs (multiple prompts × multiple models)." />
  <LinkCard title="Experiments API" href="https://docs.datadoghq.com/llm_observability/experiments/api/" description="Programmatic SDK — Python today, REST for everyone." />
  <LinkCard title="Prompt Optimization" href="https://docs.datadoghq.com/llm_observability/experiments/prompt_optimization/" description="DD suggests prompt rewrites that improve dataset scores." />
  <LinkCard title="Playground" href="https://docs.datadoghq.com/llm_observability/experiments/playground/" description="Interactive prompt + model testing without a full experiment run." />
</CardGrid>

## What's next

- [Annotation queues](../evaluations/annotation-queues/) — the upstream that feeds datasets.
- [Export API](../evaluations/export-api/) — programmatic span access that complements the experiments path.
