---
title: Operations (automation rules + cost)
description: Two DD UI features for keeping an LLM app under control once it's in production — automation rules for routine ops, cost dashboards for spend.
sidebar:
  order: 6
---

import { Aside } from '@astrojs/starlight/components';

Once you're past the "make it work" phase, two DD features do most of the day-to-day work of keeping an LLM app in line: **Automation Rules** turn observability signals into actions, and **Cost** views split LLM spend by the dimensions you actually care about.

## Automation Rules

DD's Automation Rules trigger an action when a monitor fires. For LLMObs that means turning eval signals or latency anomalies into auto-tags, Slack alerts, or pipeline gates.

**Examples we use:**

- **Auto-tag suspect traces.** Monitor: `@evaluations.meai_groundedness.value < 2`. Action: add tag `quality:suspect` to the trace. Used by the triage view in Annotation Queues.
- **Page on prompt regression.** Monitor: per-prompt-version eval-score delta > 1.5 points lower than previous version. Action: PagerDuty page (only after 50+ traces on the new version, to avoid noise from the first few).
- **Throttle on burst spend.** Monitor: `sum(llm.cost_usd) by @ml_app over 5min > $5`. Action: webhook to disable the high-volume cron via feature flag. Last-resort safety net for runaway loops.

Setup is UI-only — no code changes. The DD docs page for the feature is in the [References](#references) section below.

## Cost

DD attributes cost to LLM spans automatically when the model name is recognized in DD's pricing catalog. For Azure OpenAI deployments, the model name is what you set on the deployment (`gpt-4.1-mini`, `gpt-4o`, etc.), not the underlying SKU — so name your deployment to match the actual model for accurate cost.

**Useful Cost views:**

1. **Cost by `query.domain`** — which categories of work cost the most. Tells you whether the routing tier is making good economic decisions.
2. **Cost by `prompt.version`** — verify that the new prompt isn't accidentally expensive.
3. **Cost by `tool.name`** — tools that pull big payloads (especially the document-drafting tool) often drive cost. Sometimes the fix is a tool-side summarization step, not a prompt change.
4. **Cost per session** — `sum(llm.cost_usd) by @session.id`. Useful for the top 10 sessions in a billing cycle, to spot loops.

<Aside type="caution">
**Cost vs evaluation cost.** The Cost view sums all LLM calls including your evaluator's judge model. If you run LLM-as-judge evals on 100% of traffic, your "cost per query" doubles or worse. Sample at 5–10% in prod (see [Evaluations → External](../../evaluations/external/#sampling)) — DD's UI shows the trend whether you sample or not.
</Aside>

## Operational checklist

A short "is this LLM app actually running well?" review you can do every Monday:

1. **Trace volume by domain** — any sudden dropoff suggests a routing bug, not a traffic dip.
2. **Eval score drift** — sort prompts by score delta vs last week.
3. **Cost per query trend** — gradual creep usually means longer prompts or more tool calls per turn.
4. **Latency p95 by tool** — find the tool that's slowing down conversations. Often a downstream API issue, not yours.
5. **Annotation queue progress** — humans should be scoring at least 20 traces a week, otherwise the dataset path stalls.

## References

- [DD Automation Rules](https://docs.datadoghq.com/service_management/workflows/)
- [DD LLM cost tracking](https://docs.datadoghq.com/llm_observability/monitoring/cost_tracking/)
