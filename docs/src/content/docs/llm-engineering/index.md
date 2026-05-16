---
title: LLM Engineering Guide
description: How to instrument, monitor, evaluate, and operate an LLM application end-to-end with Datadog. InfraAdvisor's Python (ddtrace) and .NET (OpenTelemetry) services are the worked example.
sidebar:
  order: 1
  label: Overview
---

import { CardGrid, LinkCard, Aside } from '@astrojs/starlight/components';

This is the engineering guide for shipping production-grade LLM applications on Datadog. It mirrors the structure of [Datadog's LLM Observability docs](https://docs.datadoghq.com/llm_observability/) and uses InfraAdvisor — a multi-agent infrastructure-consulting app — as the worked example. Every page shows code from a real service, tabbed by language so you can read whichever stack you're shipping on.

InfraAdvisor runs two agent backends in parallel, deliberately:

- **`agent-api` (Python)** — LangChain + LangGraph; instrumented with `ddtrace.auto` and explicit `LLMObs.*` calls. Showcases the auto-instrumentation path.
- **`agent-api-dotnet` (.NET)** — Microsoft Agents Framework + Microsoft.Extensions.AI; instrumented with the OpenTelemetry SDK only, exported via OTLP to a Datadog Agent collector. Showcases the pure-OTel path.

Same product surface, two telemetry pipelines. If you're choosing a stack, the [Instrumentation](./instrumentation/python/) pages are the right place to start.

## What's in this guide

<CardGrid>
  <LinkCard title="Quickstart" href="./quickstart/" description="Your first /query showing up in LLM Observability — env vars, deploy, what to click on." />
  <LinkCard title="Instrumentation" href="./instrumentation/python/" description="ddtrace auto-instrumentation (Python) and pure OpenTelemetry SDK (.NET)." />
  <LinkCard title="Monitoring" href="./monitoring/spans-and-traces/" description="Querying spans, APM/log/DBM correlation, MCP traces, prompt tracking, metrics." />
  <LinkCard title="Evaluations" href="./evaluations/managed/" description="Managed evals, LLM-judge, external evals API, annotation queues, export API." />
  <LinkCard title="Experiments" href="./experiments/" description="Datasets, offline regression testing, prompt optimization. Roadmap item." />
  <LinkCard title="Datadog MCP server" href="./datadog-mcp/" description="Using DD's MCP server to debug both this app and its observability data." />
  <LinkCard title="Data security & RBAC" href="./security-rbac/" description="Sensitive Data Scanner, role-based access to LLMObs spans, prompt sanitization." />
  <LinkCard title="Glossary" href="./glossary/" description="LLM Observability vocabulary — span kinds, ml_app, joins, sessions." />
</CardGrid>

## When to use which DD feature

A short decision tree before you commit to a specific tool. Each row links to the page where that feature is wired up against real code:

| You want to know... | Use this | Where it lives |
|---|---|---|
| Did the agent pick the right tool? | Span tree + `agent` / `tool` span kinds | [Monitoring → Spans and traces](./monitoring/spans-and-traces/) |
| Is the answer hallucinated? | External evals (Groundedness, faithfulness) | [Evaluations → External](./evaluations/external/) |
| Did sentiment / language match? | Managed evals (DD-built) | [Evaluations → Managed](./evaluations/managed/) |
| How much did this conversation cost? | Per-trace cost rollup + Cost feature | [Monitoring → Operations](./monitoring/operations/) |
| What did a real user actually see? | Sessions + RUM session replay link | [Monitoring → Spans and traces](./monitoring/spans-and-traces/#sessions-and-rum-linking) |
| Why is this trace slow at the DB layer? | APM ↔ DBM correlation | [Monitoring → APM correlation](./monitoring/apm-correlation/) |
| Did prompt v2 regress quality? | Prompt Tracking + per-version eval scores | [Monitoring → Prompt tracking](./monitoring/prompt-tracking/) |
| Are users frustrated mid-conversation? | Composed sentiment + feedback signal | [Monitoring → Metrics](./monitoring/metrics/) |
| Will my prompt change pass regression? | Experiments + Datasets | [Experiments](./experiments/) (roadmap) |

<Aside type="tip">
**New to LLMObs?** Read [Quickstart](./quickstart/) → [Instrumentation](./instrumentation/python/) for your language → [Monitoring → Spans and traces](./monitoring/spans-and-traces/). That's the minimum viable loop. Everything else builds on it.
</Aside>

## Conventions used in this guide

- **Code samples are tabbed** between Python and .NET. If a feature only exists on one side (e.g., M.E.AI Quality evaluators are .NET-only), the page says so up front and shows only the relevant tab.
- **File paths are repo-relative**: `services/agent-api/src/...` for Python, `services/agent-api-dotnet/...` for .NET.
- **Env vars in `UPPER_CASE`** are set in `helm/values.yaml` and propagated to pod env via the chart. Pod restart is needed when they change.
- **"Wire it up" sections always cite real code**. If a sample doesn't match the file in main, the doc is stale — open an issue.
