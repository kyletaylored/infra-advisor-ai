---
title: Datadog MCP server
description: Using Datadog's own MCP server to debug both our app and its observability data — APM, LLMObs, logs, RUM, all queryable from a Claude-style client.
sidebar:
  order: 5
---

import { Aside } from '@astrojs/starlight/components';

DD ships an MCP server at [mcp.datadoghq.com](https://mcp.datadoghq.com) (separate per site — US3, US1, etc.). It exposes DD's APIs as MCP tools, so any MCP-compatible client (Claude Desktop, Cursor, your own .NET MCP host) can query traces, logs, metrics, LLMObs spans, dashboards, and more without writing API code.

For InfraAdvisor, this turns out to be the **fastest path** for two kinds of work:

1. **Debugging the app** — "find me the trace for that bad answer," "what was the latency p95 last Tuesday," "show me the slow SQL queries."
2. **Debugging our own LLM observability instrumentation** — "are evals firing for the agent?", "compare prompt v1 to v2," "is the new span source registered?"

## Setup

Add the MCP server to your client's config. For Claude Code, that's a `mcp_servers` block in settings:

```json
{
  "mcpServers": {
    "claude_ai_Datadog_MCP_US3": {
      "url": "https://us3.datadoghq.com/api/unstable/mcp/sse",
      "headers": {
        "DD-API-KEY": "<your api key>",
        "DD-APPLICATION-KEY": "<your app key>"
      }
    }
  }
}
```

Site matters — pick the one your tenant lives on. (`us1`, `us3`, `eu1`, etc.)

## What the tools do

The DD MCP server exposes ~80 tools across these categories. The ones we use most for LLMObs work:

| Tool | Use for |
|---|---|
| `search_llmobs_spans` | Find traces by `@ml_app`, `@meta.span.kind`, eval scores |
| `get_llmobs_trace` | Pull a full span tree by trace ID |
| `get_llmobs_span_details` | One span's prompt, completion, attached evals |
| `list_llmobs_evals_by_ml_app` | What evaluators are registered for an ml_app |
| `get_llmobs_eval_aggregate_stats` | Aggregate scores by evaluator over a time window |
| `get_llmobs_agent_loop` | The collapsed-decision-graph view of one trace |
| `search_datadog_spans` | APM traces (broader scope than LLMObs) |
| `analyze_datadog_logs` | Log search with aggregations |
| `get_datadog_dashboard` | Pull a dashboard's JSON for editing or comparing |

## Patterns that pay off

**"Why did this answer go wrong?"**

Paste the bad answer into the chat. The MCP-equipped assistant can:

1. `search_llmobs_spans` with `@ml_app:infra-advisor-agent-api-dotnet @output.value:"<phrase>"`.
2. Pick the matching trace, `get_llmobs_trace`.
3. Walk the span tree — which tools were called, what they returned, what the LLM did with the result.
4. Cross-reference with `analyze_datadog_logs` if a tool failed silently.

This loop takes ~30 seconds. The same investigation done by hand in DD UI takes 5–10 minutes of clicking.

**"Is my new evaluator working?"**

```
Use list_llmobs_evals_by_ml_app to confirm "my_new_check" appears.
Then get_llmobs_eval_aggregate_stats over the last hour grouped by value.
```

Tells you in one prompt whether the evaluator is registered, whether it's firing, and whether the score distribution looks sane.

**"What changed between prompt v1 and v2?"**

```
get_llmobs_eval_aggregate_stats for "meai_relevance" over last 24h grouped by @meta.prompt.version.
Show the deltas.
```

This is the [Prompt Tracking](../monitoring/prompt-tracking/) comparison without leaving your editor.

<Aside type="tip">
**Keep the API key scoped.** Create a dedicated DD application key for MCP usage with read-only scopes. Don't reuse the agent service's key — they have different blast radii.
</Aside>

## When NOT to use MCP

The MCP path is great for **investigation**. It's not great for:

- **Real-time alerting.** Use DD monitors for that — they're cheaper, faster, and integrate with paging.
- **Bulk export.** Use [Export API](../evaluations/export-api/) directly; MCP tools are scoped to single queries and don't paginate gracefully.
- **Dashboards.** Build them in DD UI. MCP can pull/edit dashboard JSON but the UI is faster for first-time creation.

## What's next

- [Spans and traces](../monitoring/spans-and-traces/) — the underlying queries the MCP tools wrap.
- [Export API](../evaluations/export-api/) — for the bulk-data path MCP isn't designed for.
