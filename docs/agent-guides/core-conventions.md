---
title: Core Conventions (Agent Guide)
parent: Development
nav_order: 5
---

# InfraAdvisor AI — Core Conventions

## Python style rules

- **Python version:** 3.12 exclusively across all services
- **Package manager:** `uv` — never `pip install` directly; always `uv add` or `uv sync`
- **Project format:** `pyproject.toml` only — no `setup.py`, no `requirements.txt`
- **Linter/formatter:** `ruff` — configured in `pyproject.toml`; runs automatically via PostToolUse hook
- **Type hints:** Required on all function signatures (parameters and return types)
- **Docstrings:** Only on public functions and classes; one-line summary style preferred
- **Line length:** 100 characters (configured in ruff)
- **Imports:** stdlib → third-party → local, separated by blank lines; `isort` enforced via ruff

## Naming conventions

### Python

| Item | Convention | Example |
|---|---|---|
| Modules | `snake_case` | `bridge_condition.py` |
| Classes | `PascalCase` | `BridgeConditionInput` |
| Functions | `snake_case` | `get_bridge_condition` |
| Constants | `UPPER_SNAKE_CASE` | `CONDITION_LABELS` |
| Environment variables | `UPPER_SNAKE_CASE` | `AZURE_OPENAI_ENDPOINT` |
| Private methods | `_snake_case` prefix | `_build_where_clause` |
| Async functions | `async def` + `snake_case` | `async def fetch_bridges` |

### Kubernetes

| Item | Convention | Example |
|---|---|---|
| Resource names | `kebab-case` | `mcp-server`, `agent-api` |
| Label key | `app` | `app: mcp-server` |
| ConfigMap names | `<service>-config` | `mcp-server-config` |
| Secret names | `<service>-secret` or descriptive | `ghcr-pull-secret` |
| Namespace | per service group | `infra-advisor`, `kafka`, `airflow`, `datadog` |

### Datadog

| Item | Convention | Example |
|---|---|---|
| Custom metric names | `<service>.<category>.<name>` | `mcp.tool.calls` |
| Metric tags | `key:value` lowercase | `tool:get_bridge_condition`, `status:success` |
| Service names | `kebab-case` | `infratools-mcp`, `infra-advisor-agent` |
| Span names | `<component>.<action>` | `agent.run`, `mcp.get_bridge_condition` |

## Structural patterns

### Every Python service entrypoint

```python
import ddtrace.auto  # MUST be first — monkey-patches all clients at import time
from ddtrace import patch_all
patch_all()

# All other imports follow
import os
import logging
from fastapi import FastAPI
# ...
```

### Environment variable access

```python
# Always use os.environ with a KeyError fail-fast — never os.getenv with a default
# for required variables
DD_AGENT_HOST = os.environ["DD_AGENT_HOST"]
AZURE_OPENAI_ENDPOINT = os.environ["AZURE_OPENAI_ENDPOINT"]

# Optional vars with defaults are acceptable
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
```

### Error handling in MCP tools

```python
# Return structured errors — never raise bare exceptions to callers
try:
    result = await fetch_data()
except httpx.HTTPStatusError as e:
    return {"error": str(e), "source": "bts_arcgis", "retriable": e.response.status_code >= 500}
except httpx.RequestError as e:
    return {"error": str(e), "source": "bts_arcgis", "retriable": True}
```

### Datadog metric emission pattern

```python
from services.mcp_server.src.observability.metrics import emit_tool_call, emit_external_api
import time

async def get_bridge_condition(input: BridgeConditionInput):
    start = time.monotonic()
    status = "success"
    try:
        result = await _fetch_from_arcgis(input)
        return result
    except Exception as e:
        status = "error"
        raise
    finally:
        latency_ms = (time.monotonic() - start) * 1000
        emit_tool_call("get_bridge_condition", latency_ms, status, result_count=len(result))
```

### pyproject.toml structure

```toml
[project]
name = "<service-name>"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    # production deps here
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "respx>=0.21",
    "pytest-asyncio>=0.23",
    "ruff>=0.4",
]

[tool.ruff]
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I"]  # pycodestyle, pyflakes, isort

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

### Kubernetes Deployment template pattern

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: <service-name>
  namespace: infra-advisor
  labels:
    app: <service-name>
spec:
  replicas: 2
  selector:
    matchLabels:
      app: <service-name>
  template:
    metadata:
      labels:
        app: <service-name>
      annotations:
        ad.datadoghq.com/<service-name>.logs: '[{"source":"python","service":"<dd-service-name>"}]'
    spec:
      imagePullSecrets:
        - name: ghcr-pull-secret    # REQUIRED on every deployment
      containers:
        - name: <service-name>
          image: ghcr.io/kyletaylored/infra-advisor-ai/<service-name>:latest
          ports:
            - containerPort: <port>
          envFrom:
            - configMapRef:
                name: <service-name>-config
            - secretRef:
                name: <service-name>-secret
          env:
            - name: DD_ENV
              value: "dev"
            - name: DD_SERVICE
              value: "<dd-service-name>"
            - name: DD_VERSION
              value: "latest"
            - name: DD_AGENT_HOST
              value: "datadog-agent"
            - name: DD_TRACE_AGENT_PORT
              value: "8126"
            - name: DD_DOGSTATSD_PORT
              value: "8125"
            - name: DD_LOGS_INJECTION
              value: "true"
            - name: DD_TRACE_SAMPLE_RATE
              value: "1.0"
            - name: DD_RUNTIME_METRICS_ENABLED
              value: "true"
          livenessProbe:
            httpGet:
              path: /health
              port: <port>
            initialDelaySeconds: 15
            periodSeconds: 30
          resources:
            requests:
              memory: "<mem>"
              cpu: "<cpu>"
            limits:
              memory: "<mem-limit>"
              cpu: "<cpu-limit>"
```

## Airflow DAG conventions

```python
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.operators.http import SimpleHttpOperator
import pendulum

# Always define with explicit start_date and catchup=False
with DAG(
    dag_id="nbi_refresh",
    start_date=pendulum.datetime(2025, 1, 1, tz="UTC"),
    schedule="0 3 * * 0",  # weekly, Sunday 03:00 UTC
    catchup=False,
    tags=["ingestion", "transportation"],
) as dag:
    ...
```

## Test conventions

```python
import pytest
import respx
import httpx

# All tests for async functions use pytest-asyncio
@pytest.mark.asyncio
async def test_get_bridge_condition_returns_results():
    with respx.mock:
        respx.get("https://services.arcgis.com/...").mock(
            return_value=httpx.Response(200, json=MOCK_ARCGIS_RESPONSE)
        )
        result = await get_bridge_condition(BridgeConditionInput(state_code="48"))
        assert len(result) > 0
        assert result[0]["_source"] == "FHWA NBI"

# Test files mirror source structure
# services/mcp-server/tests/test_bridge_condition.py
#   tests services/mcp-server/src/tools/bridge_condition.py
```

## NBI field names (do not modify)

These exact field names come from the NTAD ArcGIS schema and must never be changed:

```python
NBI_FIELDS = [
    "STRUCTURE_NUMBER_008",
    "FACILITY_CARRIED_007",
    "LOCATION_009",
    "COUNTY_CODE_003",
    "STATE_CODE_001",
    "ADT_029",
    "YEAR_ADT_030",
    "DECK_COND_058",
    "SUPERSTRUCTURE_COND_059",
    "SUBSTRUCTURE_COND_060",
    "STRUCTURALLY_DEFICIENT",
    "SUFFICIENCY_RATING",
    "INSPECT_DATE_090",
    "YEAR_BUILT_027",
    "LAT_016",
    "LONG_017",
]

CONDITION_LABELS = {
    "9": "excellent", "8": "very good", "7": "good",
    "6": "satisfactory", "5": "fair", "4": "poor",
    "3": "serious", "2": "critical", "1": "imminent failure", "0": "failed"
}
```

## Secrets and security

- Never hardcode secrets, API keys, or tokens in source files
- Never commit `.env` files (`.gitignore` covers `.env` and `.env.*`)
- K8s Secrets are created at deploy time by `make create-ghcr-secret` or via `kubectl create secret`
- The file `k8s/secrets/ghcr-pull-secret.yaml` is a template with placeholder values only
- Production Bicep parameter files (`infra/bicep/parameters/production*`) are denied by settings.json

## Git conventions

- Branch names: `phase-<N>/<short-description>` e.g. `phase-1/bicep-modules`
- Commit messages: imperative mood, reference deliverable e.g. `Add NBI ingestion DAG`
- Never force-push to `main`
- PRs required for all changes to `main`; CI must be green before merge
