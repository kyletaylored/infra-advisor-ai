---
title: AI Engineering Guide — OpenTelemetry for .NET LLM apps
description: Start here. The mental model and trace anatomy that ground the rest of the guide.
sidebar:
  order: 1
  label: Overview
---

import { Aside, CardGrid, LinkCard } from '@astrojs/starlight/components';

This guide is for AI engineers building .NET applications that use LLMs and want full observability — traces, logs, metrics, evaluations, prompt versioning, and database correlation — through **OpenTelemetry** as the single source of telemetry. InfraAdvisor's .NET services are the worked example; the patterns transfer to any .NET LLM app.

If you're new to AI observability, the mental model below is the most important section. Skim the rest; return when you need to wire something up.

<CardGrid>
  <LinkCard title="Span kinds & prompt tracking" href="./span-kinds/" description="The seven span kinds we emit and how prompts get versioned." />
  <LinkCard title="Evaluations" href="./evaluations/" description="External API, annotation queues, managed evals, and the (deferred) Experiments path." />
  <LinkCard title="Business metrics" href="./metrics/" description="Counters for tool usage, conversation completion, frustration detection." />
  <LinkCard title="DBM + log correlation" href="./correlation/" description="Attribute-based correlation that doesn't need SQL-comment injection." />
  <LinkCard title="Recipes" href="./recipes/" description="Short how-tos for the most common day-to-day tasks." />
  <LinkCard title="Troubleshooting" href="./troubleshooting/" description="The bugs we hit getting here, with fixes." />
</CardGrid>

---

## 1. Why this exists

Production LLM applications fail in ways that classical observability (logs, traces, metrics) doesn't surface. A 200 OK on `/query` can return a hallucinated answer, a tool misuse, or a citation pointing to a non-existent document. Latency and error rate look fine; quality has collapsed silently.

Datadog LLM Observability (LLMObs) adds a quality layer on top of standard APM:

| Classical APM tells you | LLM Observability adds |
|---|---|
| Did the request succeed? | Was the answer correct? |
| How fast was it? | Did the agent use the right tool? |
| Did any service throw? | Did the citation match the source? |
| Token / cost totals | Per-trace cost broken out by model + tool |
| HTTP topology | Agent → LLM → Tool → Tool span hierarchy |

The path we take to get there is **100 % OpenTelemetry**: the OTel SDK is the only tracer in our pods, traces export over OTLP to a Datadog Agent collector, and the collector forwards to LLMObs. No Datadog .NET tracer runs alongside.

<Aside type="note">
**Why pure OpenTelemetry?** The Datadog .NET tracer doesn't classify gen_ai.\* spans as LLM Observability span kinds — that classification only happens on the OTLP ingest path. Running both tracers in parallel also splits trace IDs because each tracer extracts a different propagation header (DD reads `x-datadog-trace-id`, OTel reads W3C `traceparent`). Dropping the DD tracer is what unified our trace tree.
</Aside>

---

## 2. Mental model

Three things have to be true for DD LLMObs to render a useful trace:

1. **A span tree that follows OTel GenAI semantic conventions.** Operation names like `chat`, `invoke_agent`, `execute_tool`, `embeddings` are how DD classifies spans into LLMObs kinds (`llm`, `agent`, `tool`, `embedding`). DD auto-maps from `gen_ai.operation.name` — no manual mapping for the standard kinds.

2. **An `ml_app` resource attribute on every span.** DD groups traces, evals, prompts, datasets, and dashboards by `ml_app`. Without it, traces fall into the void. We set it in the in-cluster collector's `transform/llmobs` processor (mirrors `service.name`) so app code stays pure OTel.

3. **A `source=otel` resource attribute.** Tells DD this is OTel-instrumented. Without it, evaluations submitted via the external-evals API and OTel-emitted spans don't match up. Set in `TelemetrySetup.cs`.

The .NET stack we use:

```
Application code (pure OTel, no DD-tracer-specific calls)
  │
  ├─► Microsoft.Extensions.AI (M.E.AI)     ← emits chat + execute_tool + embeddings spans
  │     └─► Azure.AI.OpenAI                ← HttpClient auto-instrumented
  │
  ├─► Microsoft.Agents.AI (MAF)            ← emits invoke_agent spans
  │     └─► AgentSession (Redis JSON)
  │
  ├─► ModelContextProtocol(.AspNetCore)   ← emits MCP request spans + traceparent
  │                                          propagation across services
  ├─► Npgsql.OpenTelemetry                 ← db.system / db.statement attrs on
  │                                          every Postgres command
  └─► Custom ActivitySource                ← task, retrieval (manual spans)
        │
        ▼
   OTLP/HTTP → DatadogAgent OTel collector → DD LLM Observability + APM + DBM correlation
```

---

## 3. The trace anatomy

One `POST /query` produces eight distinct span kinds across two services, all under one trace_id:

```
[infra-advisor-ui]                                           SERVICE
RUM browser trace                                    (rum)   ← W3C traceparent injected
└── [agent-api-dotnet]
    POST /query                                      workflow ← AspNetCore
    ├── classify_domain                              task     ← manual ActivitySource
    ├── retrieve_best_practices                      retrieval ← manual
    │   └── embeddings                               embedding ← M.E.AI IEmbeddingGenerator
    └── invoke_agent                                 agent    ← MAF AIAgent
        └── chat                                     llm      ← M.E.AI IChatClient
            ├── HttpClient POST → Azure OpenAI       (http)
            └── execute_tool (e.g. get_bridge_*)     tool     ← M.E.AI UseFunctionInvocation
                └── HttpClient POST → mcp-server-dotnet (http)
                    └── [mcp-server-dotnet]          ↓ traceparent
                        POST /mcp/                   workflow ← AspNetCore
                          └── tools/call <name>      (mcp)    ← ModelContextProtocol SDK
                              └── HttpClient → upstream (http) ← FHWA / EPA / EIA / etc.
```

The **single trace_id** is the keystone. W3C `traceparent` propagates: browser RUM → agent → MCP server → upstream API. Click any span in DD LLMObs, see the full lineage. Click "View session replay" from the trace to see the user's actual click stream.

---

<Aside type="note">
**Where this guide stops:** Wave 2 changes (React 18→19, Azure.Search.Documents 11→12, Npgsql 9→10, full LLM-judge evaluator suite, Python Experiments runner) aren't yet in production. When those land, the matching sub-page is the right place to add their goal / wire-up / outcome sections.
</Aside>
