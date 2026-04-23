---
title: Observability
nav_order: 5
has_children: true
---

# Observability

InfraAdvisor AI is instrumented end-to-end with Datadog. Every layer — browser interactions, HTTP requests, LLM reasoning, tool calls, database queries, data pipelines, and Kafka streams — produces correlated telemetry.

The design goal: from any Datadog surface, you can navigate to any other correlated signal in one click.

## Signal correlation map

```
Browser action (RUM)
  │  RUM session ID → X-DD-RUM-Session-ID header
  ▼
HTTP request (APM)
  │  Distributed trace headers (X-Datadog-Trace-Id)
  ▼
Agent API span (APM + LLM Obs)
  │  session.id = RUM session ID
  │  LLM Obs workflow span contains:
  │    → router agent span
  │    → planner agent span
  │    → specialist agent span
  │         → tool call spans (MCP)
  │              → outbound HTTP spans (government APIs)
  │    → faithfulness eval task span
  ▼
Kafka produce (DSM)
  │  infra.eval.results topic
  ▼
Database query (APM + DBM)
  │  SQL comment contains trace context
  ▼
Airflow task log (structured JSON)
  │  dd.trace_id / dd.span_id in every log line
  ▼
Azure Blob upload (APM custom span)
```

## Datadog features in use

| Feature | What it monitors | Navigate to |
|---------|-----------------|------------|
| **APM** | HTTP traces, SQL queries, outbound API calls, Redis ops | APM → Traces |
| **LLM Observability** | Agent span trees, token usage, cost, session grouping, user feedback evals, faithfulness scores | LLM Observability |
| **RUM** | Browser performance, session replay, custom events, JS errors | RUM → Sessions |
| **Data Jobs Monitoring** | Airflow DAG run duration, task status, dataset lineage | Data Observability → Data Jobs |
| **Data Streams Monitoring** | Kafka topic throughput, consumer lag, producer/consumer topology | Data Streams |
| **Database Monitoring** | PostgreSQL slow queries, EXPLAIN plans correlated to APM traces | Databases |
| **Infrastructure** | Node/pod CPU/RAM, container restarts, network I/O | Infrastructure |
| **CSPM** | Azure posture compliance baseline | Security → CSPM |
| **CWS** | Runtime threat detection on AKS nodes | Security → CWS |
| **ASM** | Application security threats, SCA, IAST | Security → ASM |
| **SBOM** | Container + host software bill of materials | Security → SBOM |

## Unified Service Tagging

All pods use the standard Datadog UST labels for consistent filtering across every surface:

```yaml
# K8s pod labels (on all Deployments)
tags.datadoghq.com/env: dev
tags.datadoghq.com/service: agent-api    # varies per service
tags.datadoghq.com/version: latest

# Environment variables
DD_ENV: dev
DD_SERVICE: agent-api
DD_VERSION: latest
```

| Service | `DD_SERVICE` |
|---------|-------------|
| MCP Server | `mcp-server` |
| Agent API | `agent-api` |
| Auth API | `auth-api` |
| Load Generator | `load-generator` |
| Airflow Scheduler | `airflow-scheduler` |
| Airflow Triggerer | `airflow-triggerer` |
| Airflow DAG Processor | `airflow-dag-processor` |

## Sections in this chapter

- [APM & Tracing](apm) — Span coverage by service, log-trace correlation, DBM, code origin, error trace linking
- [LLM Observability](llm-observability) — Multi-level span tree, auto vs explicit instrumentation, session linking, user feedback evaluations, faithfulness scoring
- [RUM & Session Replay](rum) — SDK initialization, custom events, RUM→LLM Obs linking, distributed tracing, sourcemaps
- [Dashboards & Monitors](dashboards) — All 5 dashboards, 3 monitors, 1 Synthetics test — purpose, widgets, and import instructions
