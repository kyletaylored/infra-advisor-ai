---
title: Metrics
description: Business counters that ride alongside the trace pipeline — tool usage, conversation completion, feedback ratios, frustration composites.
sidebar:
  order: 5
---

import { Tabs, TabItem, Aside } from '@astrojs/starlight/components';

Traces capture every detail of one request; metrics capture aggregate behavior across millions. Both pipelines hang off the same OTel/ddtrace SDK, so emitting a counter is a one-liner — but the question of **what** to count is the interesting part.

## What to count

Three categories worth tracking from day one:

| Counter | What it tells you | Tag dimensions |
|---|---|---|
| `infra_advisor.conversation.completed` | Per-domain conversation volume | `query.domain` |
| `infra_advisor.tool.invoked` | Which tools the agent actually uses | `tool.name`, `query.domain` |
| `infra_advisor.feedback.submitted` | User-perceived quality | `rating` (positive/negative) |

These are the **business** signals. Latency, token counts, error rate, and cost are already in your APM/LLMObs spans — don't duplicate them as metrics.

## Emitting counters

<Tabs syncKey="lang">
  <TabItem label="Python (ddtrace)">

The DD agent runs DogStatsD on UDP 8125. Send counters with `ddtrace.internal.dogstatsd`:

```python
# services/agent-api/src/observability/llm_obs.py (excerpt)
from ddtrace.internal.dogstatsd import get_dogstatsd_client

statsd = get_dogstatsd_client(
    f"{os.environ.get('DD_AGENT_HOST', 'localhost')}:"
    f"{os.environ.get('DD_DOGSTATSD_PORT', '8125')}"
)

# In the /query handler:
statsd.increment(
    "infra_advisor.conversation.completed",
    tags=[f"query.domain:{domain}"],
)

# Or as a gauge for the faithfulness eval score:
statsd.gauge(
    "eval.faithfulness_score",
    score,
    tags=[f"session_id:{session_id}", f"query.domain:{domain}"],
)
```

Counters are increment-only; gauges set a value. DD aggregates per-tag automatically.

  </TabItem>
  <TabItem label=".NET (OpenTelemetry)">

All counters use `IMeterFactory.Create(...)` on the same `ActivitySource` name that emits traces — one OTel pipeline exports both:

```csharp
// services/agent-api-dotnet/Services/AgentService.cs (excerpt)
public class AgentService
{
    private readonly Counter<long> _conversationCounter;
    private readonly Counter<long> _toolCounter;

    public AgentService(IMeterFactory meterFactory, ...)
    {
        var meter = meterFactory.Create(TelemetrySetup.ActivitySourceName);
        _conversationCounter = meter.CreateCounter<long>("infra_advisor.conversation.completed");
        _toolCounter         = meter.CreateCounter<long>("infra_advisor.tool.invoked");
    }

    public async Task<string> RunAgentAsync(string query, string domain)
    {
        // ... run agent ...
        _conversationCounter.Add(1, new KeyValuePair<string, object?>("query.domain", domain));
        foreach (var tool in toolsCalled)
            _toolCounter.Add(1,
                new KeyValuePair<string, object?>("tool.name", tool),
                new KeyValuePair<string, object?>("query.domain", domain));
        return answer;
    }
}
```

The OTel `MeterProvider` (registered in `TelemetrySetup.cs`) exports to the collector. No further config.

  </TabItem>
</Tabs>

## Frustration detection (composable)

"Is this user frustrated?" is a question best answered by **composing** existing signals, not by adding a `frustrated:true` flag in code:

- Sentiment from managed evals (`@evaluations.sentiment.value`).
- Conversation continuity (`gen_ai.conversation.id`, which MAF and ddtrace's LangChain integration emit automatically).
- Feedback counter (`infra_advisor.feedback.submitted{rating:negative}`).

Build a DD monitor on the composition:

```
avg(@evaluations.sentiment.value) by @conversation.id < 0.3
  AND sum(infra_advisor.feedback.submitted{rating:negative}) by @conversation.id >= 1
```

Keep the inputs orthogonal and let the alert rule do the joining. If two signals disagree, you can see why; with a hard-coded `frustrated:true` flag, the rationale is gone.

<Aside type="tip">
**Don't tag counters by user_id.** That's a high-cardinality field — DD bills on tag cardinality. Use `session.id` only when you specifically need per-session aggregation, and consider sampling.
</Aside>

## What's next

- [Operations](../operations/) — automation rules and cost management that lean on these counters.
- [Evaluations → External](../../evaluations/external/) — attaching scores per-trace that feed back into the metrics.
