---
title: Business metrics & frustration detection
description: Custom counters for tool usage, conversation completion, and feedback — plus how to compose them into a frustration signal.
sidebar:
  order: 4
  label: Metrics
---

## Business metrics

**Goal:** track AI effectiveness alongside infrastructure metrics — tool usage frequency, conversation completion rate, feedback ratio.

| Counter | Tagged with | Emitted from |
|---|---|---|
| `infra_advisor.conversation.completed` | `query.domain` | `AgentService.RunAgentAsync` |
| `infra_advisor.tool.invoked` | `tool.name`, `query.domain` | per `FunctionCallContent` |
| `infra_advisor.feedback.submitted` | `rating` | `/feedback` endpoint |

All counters use `IMeterFactory.Create(TelemetrySetup.ActivitySourceName)` — same meter the OTel pipeline already exports, no extra wiring.

**Extending it** — adding a new counter:

```csharp
// In whatever service emits the signal:
private readonly Counter<long> _myCounter;
public MyService(IMeterFactory meterFactory)
{
    var meter = meterFactory.Create(TelemetrySetup.ActivitySourceName);
    _myCounter = meter.CreateCounter<long>("infra_advisor.my_event");
}

// At the event site:
_myCounter.Add(1, new KeyValuePair<string, object?>("dimension", value));
```

The OTel `MeterProvider` already exports to the collector — no further config needed.

## Frustration detection (composable)

**Goal:** identify users having a bad conversation before they churn.

Compose from signals already in production:
- Sentiment managed eval (see [Evaluations](../evaluations/#managed-evaluations-dd-built-ui-enabled)).
- Conversation grouping by `gen_ai.conversation.id` (MAF `AgentSession` emits this automatically).
- Feedback events (`infra_advisor.feedback.submitted{rating:negative}`).

Build a DD monitor on the combination:
```
avg(sentiment_score) by conversation.id < 0.3 over last 5 turns
  AND count(feedback.rating:negative) by conversation.id >= 1
```

The point: frustration is a *composition*, not a primary signal. Don't add a `frustrated:true` flag in code — keep the inputs orthogonal and let the alert rule do the joining.
