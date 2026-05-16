---
title: Export API
description: Programmatic access to LLM Observability data — pull spans, scores, and annotations for offline analysis or to feed external evaluators.
sidebar:
  order: 5
  label: Export API
---

import { Aside } from '@astrojs/starlight/components';

DD's [Spans Export API](https://docs.datadoghq.com/llm_observability/instrumentation/api/?tab=model#search-spans) lets you query LLMObs data the same way the UI does, and stream the results back as JSON. It's how you bridge "DD has the data" to "I want to do something with it offline" — feed external evaluators, build a labeled dataset, run cohort analysis in a notebook, archive for compliance.

## When you need it

The three places this API earns its keep:

1. **External evaluators that don't live in the agent service.** Pull last-hour traces matching a filter, run an external scoring pipeline, POST the scores back via the eval-metric API (see [External](../external/)).
2. **Dataset construction.** Pull traces with `@evaluations.quality.value:good` from the annotation queue, dedupe, write to your dataset pipeline.
3. **Offline analytics.** Pull a week's worth of traces, group by `query.domain` and `prompt.version`, do cohort math your DD dashboard can't.

## The shape

```http
POST https://api.datadoghq.com/api/v2/spans/events/search
DD-API-KEY: <key>
DD-APPLICATION-KEY: <key>
Content-Type: application/json

{
  "filter": {
    "query": "@ml_app:infra-advisor-agent-api-dotnet @meta.span.kind:agent",
    "from":  "now-1h",
    "to":    "now"
  },
  "options": {
    "timezone": "UTC"
  },
  "page": { "limit": 1000 },
  "sort": "-timestamp"
}
```

The response includes full span content (prompt, completion, tool calls), attached evals, parent/child IDs for reconstructing trace trees, and pagination cursors for follow-on requests.

## Common patterns

**Pull all evaluator misses for one prompt version:**

```python
import requests

resp = requests.post(
    "https://api.datadoghq.com/api/v2/spans/events/search",
    headers={
        "DD-API-KEY": os.environ["DD_API_KEY"],
        "DD-APPLICATION-KEY": os.environ["DD_APP_KEY"],
    },
    json={
        "filter": {
            "query": (
                "@ml_app:infra-advisor-agent-api-dotnet "
                "@meta.prompt.version:v2-a2c4f1d8 "
                "@evaluations.meai_groundedness.value:<2"
            ),
            "from": "now-7d",
            "to":   "now",
        },
        "page": {"limit": 1000},
    },
)
for trace in resp.json()["data"]:
    # Pipe into your dataset builder...
    ...
```

**Sync to an offline data store nightly** — same call but `from: "now-24h"`, paginate until empty, write to S3 / Parquet / wherever.

## Rate limits & cost

- The API charges DD API quota, not per-eval. Pulling 10k spans is ~10 calls (1000 per page).
- The data you pull stays in DD's storage until DD's retention expires — the export doesn't delete or "consume" anything.
- DD throttles to a few requests per second per app key. Spread queries if you have lots of pulls.

<Aside type="caution">
**Don't put `DD_APPLICATION_KEY` in the agent's pod env.** It's a high-privilege key. Keep export jobs in a separate workload (CronJob, offline notebook) with limited access. The agent service only needs `DD_API_KEY` for posting spans + evals — never for reading.
</Aside>

## What's next

- [Developer guide](../developer-guide/) — building your own evaluator pipeline that uses Export API as input.
- [Experiments](../../experiments/) — the dataset feature this often feeds into.
