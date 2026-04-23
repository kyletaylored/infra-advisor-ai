---
title: Conventions
parent: Development
nav_order: 3
---

# Conventions

## Python

- **Python 3.12** across all services
- **Package manager:** `uv` — use `uv sync` to install, `uv run` to execute
- **Project file:** `pyproject.toml` in each service root
- **Linter/formatter:** `ruff` — line length 100, auto-format on save
- **Type hints:** Required on all function signatures
- **Imports:** `import ddtrace.auto` must be the **first import** in every service entrypoint (`main.py`)

### Naming

| Thing | Convention | Example |
|-------|-----------|---------|
| Modules/files | `snake_case` | `bridge_condition.py` |
| Functions | `snake_case` | `get_bridge_condition()` |
| Classes | `PascalCase` | `DDJsonFormatter` |
| Constants / env vars | `UPPER_SNAKE_CASE` | `AZURE_SEARCH_INDEX_NAME` |
| Private helpers | Leading underscore | `_build_date_range()` |

### Environment variables

Never hardcode secrets or credentials. Always use:

```python
import os

AZURE_SEARCH_ENDPOINT = os.environ["AZURE_SEARCH_ENDPOINT"]  # Fail fast on missing
OPTIONAL_KEY = os.environ.get("OPTIONAL_KEY")                # Returns None if missing
```

`os.environ["VAR"]` (not `.get()`) is used for required variables — the KeyError on startup is the correct failure mode (immediate, clear, not a subtle runtime error).

### Error handling

Return structured error dicts from MCP tools rather than raising exceptions:

```python
return {
    "error": "EIA API returned 429 Too Many Requests",
    "action": "Retry in 60 seconds",
    "retriable": True
}
```

Use `retriable: false` for errors the LLM should not retry (index missing, auth failure, out-of-scope request).

### Datadog instrumentation patterns

```python
# 1. First line of every entrypoint
import ddtrace.auto  # noqa: F401

# 2. Custom metric emission
from datadog import statsd
statsd.increment("mcp.tool.calls", tags=["tool:get_bridge_condition", "status:success"])

# 3. Manual span for unmeasured operations
from ddtrace import tracer
with tracer.trace("azure.blob.upload", service="airflow-scheduler") as span:
    span.set_tag("blob.path", blob_path)
    span.set_tag("dag.id", dag_id)
    blob_client.upload_blob(data, overwrite=True)
```

## Kubernetes

### Resource naming

- All resources: `kebab-case`
- Service names match deployment names (e.g., deployment `agent-api`, service `agent-api`)
- ConfigMaps: `<service>-config`
- Secrets: `<service>-secret`

### Required labels

Every Deployment manifest must include:
```yaml
labels:
  app: <service-name>
  tags.datadoghq.com/env: dev
  tags.datadoghq.com/service: <service-name>
  tags.datadoghq.com/version: latest
```

### imagePullSecrets

Every Deployment and CronJob that pulls from GHCR must include:
```yaml
spec:
  template:
    spec:
      imagePullSecrets:
        - name: ghcr-pull-secret
```

### Namespaces

| Workload type | Namespace |
|--------------|-----------|
| Application services | `infra-advisor` |
| Airflow | `airflow` |
| Kafka | `kafka` |
| Datadog | `datadog` |

## Datadog

### Service names

| Service | `DD_SERVICE` value |
|---------|--------------------|
| MCP Server | `mcp-server` |
| Agent API | `agent-api` |
| Auth API | `auth-api` |
| Load Generator | `load-generator` |
| Airflow Scheduler | `airflow-scheduler` |

### Metric naming

Format: `<service>.<category>.<metric_name>` — all lowercase, dots as separators.

Examples:
- `mcp.tool.calls` (count)
- `mcp.tool.latency_ms` (timing)
- `mcp.external_api.latency_ms` (timing)
- `airflow.dag.blob_upload_latency_ms` (timing)

### Span naming

Use dot-separated lowercase: `azure.blob.upload`, `load_generator.run`, `faithfulness-eval`

## Airflow DAGs

```python
# Standard DAG header
import pendulum
from airflow.decorators import dag, task

@dag(
    dag_id="nbi_refresh",
    schedule="0 3 * * 0",      # Use schedule= (Airflow 3.x), not schedule_interval=
    start_date=pendulum.datetime(2024, 1, 1, tz="UTC"),
    catchup=False,              # Never backfill
    tags=["ingestion", "transportation"],
)
def nbi_refresh():
    ...
```

**Key rules:**
- No `schedule_interval=` (Airflow 2 deprecated, removed in 3.x)
- `catchup=False` always
- Use `@task` decorator (TaskFlow API)
- All third-party imports (pandas, Azure SDK, openai) must be **inside task function bodies** — the dag-processor scans DAG files with a minimal Python environment that does not have these packages installed
- No top-level `import ddtrace.auto` — dag-processor does not have ddtrace

## Git

### Branch naming

```
phase-N/<short-description>   # phase work
fix/<short-description>       # bug fixes
feat/<short-description>      # features
```

### Commit messages

Imperative mood, present tense, under 72 characters:
```
Add NBI bridge refresh DAG with ArcGIS pagination
Fix scheduler CrashLoopBackOff from pyspark startup timeout
Update FEMA tool to return structured error on 404
```

### PR requirements

- All PRs require passing CI (pytest matrix + TypeScript build)
- `main` branch is protected — no direct pushes
- Merging to `main` triggers automatic Docker build + AKS rolling deploy

## NBI field names

The exact FHWA field names from the National Bridge Inventory schema must be preserved exactly in code, documentation, and tests. Do not rename, abbreviate, or camelCase them. See the [NBI Refresh DAG](../data-pipeline/nbi-refresh) for the complete field reference.
