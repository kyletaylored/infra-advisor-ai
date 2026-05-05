---
title: APM & Tracing
parent: Observability
nav_order: 1
---

# APM & Distributed Tracing

All Python services and the Airflow scheduler use `ddtrace` for automatic instrumentation. `import ddtrace.auto` is the **first import** in every Python service entrypoint.

The .NET services (`agent-api-dotnet`, `mcp-server-dotnet`) use **OpenTelemetry** with OTLP HTTP export to the Datadog Agent at port 4318. This is the standard path for non-Python Datadog customers — no ddtrace involved.

## Span coverage — Python services

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

## Span coverage — .NET services

The .NET services share the same `OTEL_EXPORTER_OTLP_ENDPOINT` (port 4318 on the Datadog Agent) and emit spans that appear in Datadog APM alongside the Python services.

### Agent API (.NET) (`agent-api-dotnet`)

| Span type | Source | Tags |
|-----------|--------|------|
| HTTP request | `AddAspNetCoreInstrumentation` (auto) | `http.method`, `http.url`, `http.status_code` |
| Outbound HTTP (MCP Server) | `AddHttpClientInstrumentation` (auto) | `http.url`, `peer.hostname` |
| PostgreSQL queries | `AddNpgsql()` (auto) | `db.statement`, `db.system=postgresql` |
| Redis commands | StackExchange.Redis (manual via ActivitySource) | |
| LLM span (router + specialists) | `LlmTelemetry.StartLlmActivity` (custom) | `openinference.span.kind=LLM`, `gen_ai.system`, `gen_ai.request.model`, `gen_ai.prompt.0.content` |

### MCP Server (.NET) (`mcp-server-dotnet`)

| Span type | Source | Tags |
|-----------|--------|------|
| HTTP request | `AddAspNetCoreInstrumentation` (auto) | |
| Outbound HTTP (government APIs) | `AddHttpClientInstrumentation` (auto) | |
| Azure AI Search | `AddHttpClientInstrumentation` (auto, via REST calls) | |

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

`DD_DBM_PROPAGATION_MODE=full` is set on all services that use PostgreSQL directly: `auth-api`, `agent-api`, and `agent-api-dotnet`. This injects full trace context as SQL comments, enabling **"View Trace"** links from every DBM query sample back to the originating APM trace.

**Python services** (ddtrace): propagation is injected automatically by `psycopg2` instrumentation:

```sql
/*dddbs='agent-api',dde='dev',ddh='agent-api-pod',ddps='agent-api',ddpv='latest',
traceparent='00-3421959702764693-8721043291846321-01'*/
SELECT * FROM conversations WHERE user_id = $1
```

**agent-api-dotnet** (Npgsql + OTel): `AddNpgsql()` in the OTel tracing pipeline captures database spans. The `DD_DBM_PROPAGATION_MODE=full` variable is present in the configmap for future Npgsql DDM propagation support — Npgsql DB spans already appear in APM via OTel.

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
browser → ui/nginx → agent-api         → mcp-server         → [arcgis, openfema, eia, epa, samgov, tavily]
                   → agent-api-dotnet  → mcp-server-dotnet  → [arcgis, openfema, eia, epa, samgov, tavily]
                   → auth-api          → postgres
                   → postgres (conversations)
                   → redis
                   → kafka
airflow-scheduler  → [openai, blob, search]
load-generator     → kafka
```

Python services appear as `ddtrace`-instrumented services. .NET services appear as OpenTelemetry-sourced services under the same `dd.env=dev` tag. Both are visible in the same service map.
