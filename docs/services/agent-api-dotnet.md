---
title: Agent API (.NET)
parent: Services
nav_order: 3
---

# Agent API (.NET)

**Port:** 8001 | **Framework:** ASP.NET Core 10 (minimal API) | **Replicas:** 2

A full .NET 10 port of the [Python Agent API](agent-api). Implements the same multi-agent routing architecture, the same 9 core endpoints, the same Redis session memory model, and the same PostgreSQL conversation persistence. Traces are emitted via **OpenTelemetry OTLP** (not ddtrace) to the Datadog Agent at port 4318.

## When to use the .NET backend

Select **.NET** in the UI backend switcher when demonstrating or benchmarking the .NET implementation. Both backends are functionally equivalent from the user's perspective — the selection persists in `localStorage` and can be changed at any time.

## Multi-agent architecture

Identical routing to the Python version:

```
POST /query
  │
  ├── Router Agent (Azure OpenAI JSON mode)
  │     Classifies domain: engineering | water_energy | business_development | document | general
  │
  └── Specialist Agent (ReAct loop, up to 10 turns)
        Receives curated tool subset for its domain
        Calls MCP Server (.NET) via JSON-RPC 2.0 over HTTP
```

## API endpoints

The .NET backend exposes the same endpoint contract as the Python version. See [Agent API (Python)](agent-api) for full request/response schemas. Quick reference:

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/query` | Run multi-agent pipeline; accepts `X-Conversation-ID` + `X-User-ID` |
| `POST` | `/suggestions` | Contextual follow-up suggestions (LLM-powered) |
| `GET` | `/suggestions/initial` | Opening suggestions from Redis pool |
| `GET` | `/models` | Available Azure OpenAI deployments |
| `GET` | `/tools` | List MCP tools available to this backend |
| `POST` | `/tools/{name}` | Direct MCP tool invocation (sandbox) |
| `POST` | `/feedback` | Record user feedback |
| `GET` | `/health` | Readiness: MCP and LLM connectivity |
| `DELETE` | `/session/{id}` | Clear Redis session memory |
| `POST` | `/conversations` | Create conversation record in PostgreSQL |
| `GET` | `/conversations` | List conversations for user |
| `GET` | `/conversations/{id}` | Fetch conversation with message history |
| `DELETE` | `/conversations/{id}` | Delete conversation |

## Conversation persistence

Requires `DATABASE_URL` set in the `agent-api-dotnet-secret` K8s Secret. When set, tables are created on startup (idempotent). When unset, conversation endpoints return `503` and `/query` still works normally.

```bash
make create-agent-api-dotnet-secret   # uses DATABASE_URL from .env if set
```

See [Agent API (Python) — Conversation persistence](agent-api#conversation-persistence) for the full schema; both services share the same PostgreSQL tables. The `backend` column distinguishes which service created each conversation (`"python"` vs `"dotnet"`).

## Observability

**Tracing:** OpenTelemetry OTLP HTTP to `datadog-agent.datadog.svc.cluster.local:4318`.

| Span | Instrumented by | Key tags |
|------|----------------|----------|
| HTTP requests | `AddAspNetCoreInstrumentation` (auto) | `http.method`, `http.route` |
| Outbound HTTP (MCP, Azure OpenAI) | `AddHttpClientInstrumentation` (auto) | `http.url` |
| PostgreSQL (conversations) | `AddNpgsql()` (auto) | `db.statement`, `db.system=postgresql` |
| LLM router + specialist calls | `LlmTelemetry.StartLlmActivity` (custom) | `openinference.span.kind=LLM`, `gen_ai.request.model`, `gen_ai.prompt.0.content` |

**Service name:** `infraadvisor-agent-api-dotnet` (set via `OTEL_SERVICE_NAME` in configmap).

**DBM:** `DD_DBM_PROPAGATION_MODE=full` is set in the configmap. Npgsql database spans appear in APM via OTel. Full SQL comment injection for DBM → APM linking is available once Npgsql adds DDM propagation support.

## Configuration

Environment variables (from ConfigMap + Secret):

| Variable | Source | Description |
|----------|--------|-------------|
| `AZURE_OPENAI_ENDPOINT` | Secret | Azure OpenAI resource URL |
| `AZURE_OPENAI_API_KEY` | Secret | Azure OpenAI API key |
| `DATABASE_URL` | Secret | PostgreSQL connection string (optional) |
| `AZURE_OPENAI_DEPLOYMENT` | ConfigMap | Default model deployment name |
| `AVAILABLE_MODELS` | ConfigMap | Comma-separated list of available models |
| `MCP_SERVER_URL` | ConfigMap | Points to `mcp-server-dotnet` (not Python MCP) |
| `REDIS_HOST` / `REDIS_PORT` | ConfigMap | Session memory |
| `KAFKA_BOOTSTRAP_SERVERS` | ConfigMap | Eval event stream |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | ConfigMap | `http://datadog-agent.datadog:4318` |
| `DD_DBM_PROPAGATION_MODE` | ConfigMap | `full` |

## Build and run locally

```bash
cd services/agent-api-dotnet
dotnet restore
dotnet build

AZURE_OPENAI_ENDPOINT=https://... \
AZURE_OPENAI_API_KEY=... \
MCP_SERVER_URL=http://localhost:8000/mcp \
REDIS_HOST=localhost \
dotnet run --urls http://localhost:8003
```

## Tests

```bash
cd services/agent-api-dotnet
dotnet test
```

CI runs tests via the `test-dotnet` matrix job in `.github/workflows/ci.yml`.
