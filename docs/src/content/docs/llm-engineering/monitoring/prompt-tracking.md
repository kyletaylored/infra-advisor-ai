---
title: Prompt tracking
description: Version your system prompts so dashboards can compare cost, latency, and eval scores between v1 and v2 without rebuilding queries.
sidebar:
  order: 4
---

import { Tabs, TabItem } from '@astrojs/starlight/components';

When you ship prompt v2, you want to compare it to v1 across every dimension — latency, cost, eval scores — without manually filtering by date or commit hash. Prompt Tracking solves this by attaching a `name` + `version` + `template` to every LLM span. DD's "Prompts" view then groups by them automatically.

## The data shape

Each `chat` or `invoke_agent` span gets a JSON payload on the `_dd.ml_obs.prompt_tracking` attribute:

```json
{
  "name": "infra-advisor-system",
  "version": "v1-a2c4f1d8",
  "template": "<full system prompt text>",
  "variables": {}
}
```

The `version` is derived from a content hash so it changes only when the template actually changes. Convention we use: `v<major>-<sha256[:8]>`. Bump the major manually when you want a clean break for dashboards; the hash bumps automatically on any wording change.

## Wiring it up

<Tabs syncKey="lang">
  <TabItem label="Python (ddtrace)">

```python
# services/agent-api/src/agent.py (excerpt)
import hashlib, json
from ddtrace.llmobs import LLMObs

AGENT_SYSTEM_PROMPT = """..."""  # the actual prompt text

PROMPT_VERSION = "v1-" + hashlib.sha256(
    AGENT_SYSTEM_PROMPT.encode()
).hexdigest()[:8]

# Inside the agent span, after the LLM call:
LLMObs.annotate(
    span=specialist_span,
    prompt={
        "name": "infra-advisor-system",
        "version": PROMPT_VERSION,
        "template": AGENT_SYSTEM_PROMPT,
        "variables": {},
    },
)
```

`LLMObs.annotate(..., prompt=...)` writes the tracking JSON to the active span's `_dd.ml_obs.prompt_tracking` attribute.

  </TabItem>
  <TabItem label=".NET (OpenTelemetry)">

The .NET path uses a global `ActivityListener` that stamps the JSON on every `invoke_agent` and `chat` span automatically — no per-call instrumentation:

```csharp
// services/agent-api-dotnet/Program.cs (excerpt)
const string AgentSystemPrompt = """...""";

static string ShortContentHash(string s) =>
    Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(s)))
        .Substring(0, 8).ToLowerInvariant();

var promptVersion = "v1-" + ShortContentHash(AgentSystemPrompt);
var promptTrackingJson = JsonSerializer.Serialize(new {
    name     = "infra-advisor-system",
    version  = promptVersion,
    template = AgentSystemPrompt,
    variables = new Dictionary<string, object>(),
});

ActivitySource.AddActivityListener(new ActivityListener
{
    ShouldListenTo = src => src.Name is "Experimental.Microsoft.Extensions.AI"
                         or TelemetrySetup.ActivitySourceName,
    Sample = (ref ActivityCreationOptions<ActivityContext> _) =>
        ActivitySamplingResult.AllDataAndRecorded,
    ActivityStarted = activity =>
    {
        if (activity.OperationName is "chat" or "invoke_agent")
            activity.SetTag("_dd.ml_obs.prompt_tracking", promptTrackingJson);
    },
});
```

The listener is global, so adding a new agent that uses the same prompt automatically gets tracked — one less thing to remember.

  </TabItem>
</Tabs>

## What you see in DD

**DD → LLM Observability → Prompts** lists every `(name, version)` combination DD has seen, with traffic counts, latency, token totals, cost, and the average eval scores. Pick two versions, click "Compare", and you get a side-by-side: prompt diff on the left, metric deltas on the right.

The view works without further wiring once `_dd.ml_obs.prompt_tracking` is on the spans.

## Common patterns

- **A/B test a prompt:** define two prompt constants, hash both, pick one per request (random or sticky per user-ID hash), stamp the chosen version's JSON on the activity. The Prompts view groups them automatically.
- **Promote a prompt:** bump the major version (`v2-...`) when the change is significant. Keeps dashboards bucketed by intent, not by typo-fix.
- **Track regressions:** in DD UI, add an SLO on "avg eval score per prompt version > 4.0". Alerts when a new version is materially worse.
- **Variable templates:** when your prompt is parameterized (e.g., conversation history is interpolated), put the parameter values into `variables`, not the templated text. DD diffs the template; variables are per-call metadata.

## What's next

- [Evaluations → Managed](../../evaluations/managed/) — the eval scores that Prompt Tracking groups by.
- [Spans and traces](../spans-and-traces/) — `@meta.prompt.version:v2-*` queries for regression hunting.
