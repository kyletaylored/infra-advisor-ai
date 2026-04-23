---
title: APM & Tracing
parent: Observability
nav_order: 1
---

# APM & Distributed Tracing

All four Python services and the Airflow scheduler use `ddtrace` for automatic instrumentation. `import ddtrace.auto` is the **first import** in every service entrypoint — before any framework or library imports.

## Span coverage

### MCP Server (`mcp-server`)

| Span type | Source | Tags |
|-----------|--------|------|
| HTTP request | FastAPI (auto) | `http.method`, `http.url`, `http.status_code` |
| Outbound HTTP (ArcGIS, FEMA, EIA, EPA, SAM.gov, etc.) | httpx (auto) | `http.url`, `peer.hostname` |
| Azure AI Search | azure-search-documents (auto) | index name, operation |
| Outbound HTTP (Tavily) | httpx (auto) | |

### Agent API (`agent-api`)

| Span type | Source | Tags |
|-----------|--------|------|
| HTTP request | FastAPI (auto) | |
| Redis commands | redis-py (auto) | `db.type`, `db.statement` |
| Outbound HTTP (MCP Server) | httpx (auto) | |
| Kafka produce | confluent-kafka (auto) | `messaging.destination`, `messaging.system` |
| LangChain chat model | langchain (auto) | model, token counts |
| LangGraph executor | langgraph (auto) | |

### Auth API (`auth-api`)

| Span type | Source | Tags |
|-----------|--------|------|
| HTTP request | FastAPI (auto) | |
| PostgreSQL queries | psycopg2 (auto) | `db.statement`, `db.type` |
| DBM propagation | ddtrace (auto) | Full trace context in SQL comments |

### Load Generator (`load-generator`)

| Span type | Source | Tags |
|-----------|--------|------|
| `load_generator.run` | Manual `tracer.trace()` | `query_count` |
| Kafka produce | confluent-kafka (auto) | |

### Airflow Scheduler

| Span type | Source | Tags |
|-----------|--------|------|
| HTTP (external API calls in DAG tasks) | httpx (auto) | |
| Azure Blob Storage uploads | Manual `tracer.trace()` via `_dd_blob.py` | `blob.container`, `blob.path`, `dag.id`, `blob.size_bytes` |
| Azure AI Search upserts | azure-search-documents (auto) | |
| Azure OpenAI embeddings | openai (auto) | |

## Code origin

`DD_CODE_ORIGIN_FOR_SPANS_ENABLED=true` is set on all four service configmaps. When viewing a span in Datadog APM, the **Code Origin** section links directly to the source file and line that created the span.

## Log-trace correlation

Structured JSON logs from all services include `dd.trace_id` and `dd.span_id` fields. When you view a trace in Datadog APM, the correlated logs panel shows logs from the same trace ID.

The Airflow scheduler uses a custom `DDJsonFormatter` (defined in `airflowLocalSettings` in `k8s/airflow/values.yaml`) that injects these fields into every task log line:

```json
{
  "timestamp": "2026-04-23T05:00:12.341Z",
  "level": "INFO",
  "logger": "airflow.task",
  "message": "Fetched 2000 NBI bridge records",
  "dd.trace_id": "3421959702764693",
  "dd.span_id": "8721043291846321",
  "dd.env": "dev",
  "dd.service": "airflow-scheduler",
  "dag_id": "nbi_refresh",
  "task_id": "fetch_nbi_bridges",
  "run_id": "manual__2026-04-23T05:00:00+00:00"
}
```

`sitecustomize.py` (in `/opt/airflow/dags/`) ensures ddtrace is initialized in every LocalExecutor task subprocess (not just the scheduler main process), so task logs get trace IDs even when tasks run in separate Python processes.

## Database Monitoring (DBM)

`DD_DBM_PROPAGATION_MODE=full` is set **only** on `auth-api` (the sole service using PostgreSQL directly). This injects full trace context as SQL comments:

```sql
/*dddbs='auth-api',dde='dev',ddh='auth-api-pod',ddps='auth-api',ddpv='latest',
traceparent='00-3421959702764693-8721043291846321-01'*/
SELECT * FROM users WHERE email = $1
```

In Datadog DBM, each query sample shows a **"View Trace"** link that opens the originating APM trace.

## Error trace linking

When the Agent API returns a 500, the response body includes the ddtrace trace ID:

```json
{
  "detail": "OpenAI connection timeout",
  "trace_id": "3421959702764693"
}
```

The UI renders a **"View trace →"** link that opens `https://us3.datadoghq.com/apm/trace/{trace_id}`.

## Service map

Navigate to **Datadog → APM → Service Map** to see the auto-discovered service dependency graph:

```
browser → ui/nginx → agent-api → mcp-server → [arcgis, openfema, eia, epa, samgov, tavily]
                   → auth-api  → postgres
                   → redis
                   → kafka
airflow-scheduler  → [openai, blob, search]
load-generator     → kafka
```
