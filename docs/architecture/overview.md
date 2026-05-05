---
title: System Overview
parent: Architecture
nav_order: 1
---

# System Overview

## Service map

Seven microservices work together to serve infrastructure advisory queries. Each runs as a containerized workload on AKS, with images published to GitHub Container Registry.

The platform provides **parallel Python and .NET reasoning stacks** (Agent API + MCP Server). The UI backend switcher routes a user's requests to one stack or the other.

```
┌─────────────────────────────────────────────────────────────────────┐
│  Browser                                                             │
│  React 18 + Chakra UI + Datadog RUM                                  │
│  https://infra-advisor-ai.kyletaylor.dev                             │
└──────┬─────────────────────┬────────────────────┬───────────────────┘
       │ /auth/*             │ /api/*              │ /api-dotnet/*
       ▼                     ▼                     ▼
┌────────────────┐  ┌─────────────────────┐  ┌──────────────────────────┐
│   Auth API     │  │   Agent API         │  │   Agent API (.NET)       │
│   :8002        │  │   :8001             │  │   :8001                  │
│   FastAPI      │  │   FastAPI + LangChain│  │   ASP.NET Core 10        │
│   PostgreSQL   │  │   LangGraph ReAct   │  │   OTel/OpenInference.NET │
│   JWT + bcrypt │  │   Redis + Kafka     │  │   Redis + Kafka          │
└───────┬────────┘  └──────────┬──────────┘  └───────────┬──────────────┘
        │                      │ MCP HTTP                 │ MCP HTTP
        │                      ▼                          ▼
        │           ┌──────────────────┐       ┌──────────────────────┐
        │           │   MCP Server     │       │   MCP Server (.NET)  │
        │           │   :8000 FastMCP  │       │   :8000 ModelContextP│
        │           │   11 data tools  │       │   11 data tools      │
        │           └────────┬─────────┘       └───────────┬──────────┘
        │                    └──────────────────────────────┘
        │                              │
        ▼                   ┌──────────┴─────────┐
┌──────────────────┐        │  External APIs      │
│  PostgreSQL      │        │  FHWA NBI ArcGIS    │
│  :5432           │        │  OpenFEMA REST      │
│  User accounts   │        │  EIA API v2         │
│  Conversations   │        │  EPA SDWIS          │
│  Messages        │        │  ERCOT public API   │
└──────────────────┘        │  TxDOT Open Data    │
                            │  SAM.gov            │
                            │  USASpending.gov    │
                            │  Tavily search      │
                            └────────────────────┘

┌────────────────────────────────────────────────────────────────────┐
│  Data Pipeline (Airflow)                                           │
│  Scheduler StatefulSet — LocalExecutor — 9 DAGs                   │
│  NBI → FEMA → EIA → TWDB/EPA → Knowledge Base → ...               │
│  Azure Blob Storage (raw parquet) → Azure AI Search (vectors)     │
└────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────┐
│  Eval Loop                                                         │
│  Load Generator CronJob → Kafka infra.query.events                │
│  → Agent API consumer → Kafka infra.eval.results                  │
│  → Datadog LLM Observability faithfulness metric                  │
└────────────────────────────────────────────────────────────────────┘
```

## Namespaces

All workloads are organized into four Kubernetes namespaces:

| Namespace | Contents |
|-----------|----------|
| `infra-advisor` | mcp-server, mcp-server-dotnet, agent-api, agent-api-dotnet, auth-api, ui, redis, postgres, mailhog, load-generator |
| `airflow` | Airflow scheduler, API server, dag-processor, triggerer, PostgreSQL |
| `kafka` | Strimzi Operator, Kafka cluster, topics |
| `datadog` | Datadog Agent DaemonSet, Cluster Agent |

## Inter-service communication

All service-to-service traffic uses Kubernetes DNS names (`<service>.<namespace>.svc.cluster.local`):

| From | To | Protocol | DNS Name |
|------|----|----------|----------|
| nginx (UI) | Agent API (Python) | HTTP | `agent-api.infra-advisor.svc.cluster.local:8001` |
| nginx (UI) | Agent API (.NET) | HTTP | `agent-api-dotnet.infra-advisor.svc.cluster.local:8001` |
| nginx (UI) | Auth API | HTTP | `auth-api.infra-advisor.svc.cluster.local:8002` |
| Agent API (Python) | MCP Server (Python) | HTTP (MCP) | `mcp-server.infra-advisor.svc.cluster.local:8000` |
| Agent API (.NET) | MCP Server (.NET) | HTTP (MCP) | `mcp-server-dotnet.infra-advisor.svc.cluster.local:8000` |
| Agent API (both) | Redis | Redis protocol | `redis.infra-advisor.svc.cluster.local:6379` |
| Agent API (both) | Kafka | Kafka protocol | `kafka-cluster-kafka-bootstrap.kafka.svc.cluster.local:9092` |
| Agent API (both) | PostgreSQL | PostgreSQL | `postgres.infra-advisor.svc.cluster.local:5432` |
| Load Generator | Kafka | Kafka protocol | `kafka-cluster-kafka-bootstrap.kafka.svc.cluster.local:9092` |
| Airflow | Azure OpenAI | HTTPS | `*.openai.azure.com` |
| Airflow | Azure AI Search | HTTPS | `*.search.windows.net` |
| Airflow | Azure Blob Storage | HTTPS | `*.blob.core.windows.net` |
| Python services | Datadog Agent | UDP/TCP | `datadog-agent.datadog.svc.cluster.local:8125/8126` |
| .NET services | Datadog Agent (OTLP) | HTTP | `datadog-agent.datadog.svc.cluster.local:4318` |

## Container images

All images are hosted at `ghcr.io/kyletaylored/infra-advisor-ai/<service>:latest` and built by GitHub Actions on every merge to `main`.

| Service | Image | Language | Base |
|---------|-------|----------|------|
| MCP Server | `mcp-server` | Python 3.12 | `python:3.12-slim` |
| MCP Server (.NET) | `mcp-server-dotnet` | .NET 10 | `mcr.microsoft.com/dotnet/aspnet:10.0` |
| Agent API | `agent-api` | Python 3.12 | `python:3.12-slim` |
| Agent API (.NET) | `agent-api-dotnet` | .NET 10 | `mcr.microsoft.com/dotnet/aspnet:10.0` |
| Auth API | `auth-api` | Python 3.12 | `python:3.12-slim` |
| Load Generator | `load-generator` | Python 3.12 | `python:3.12-slim` |
| UI | `ui` | TypeScript/nginx | `node:24-alpine` → `nginx:alpine` |

## Ingress

A single nginx Deployment (in the `ui` pod) acts as the ingress reverse proxy for the entire application:

| Path prefix | Proxied to | Notes |
|-------------|-----------|-------|
| `/` | nginx static files | React SPA (`/assets/`) |
| `/api/*` | Agent API (Python) `:8001` | Strips `/api/` prefix |
| `/api-dotnet/*` | Agent API (.NET) `:8001` | Strips `/api-dotnet/` prefix |
| `/auth/*` | Auth API `:8002` | Strips `/auth/` prefix |
| `/airflow/*` | Airflow API Server `:8080` | Preserves `/airflow/` prefix |
| `/mailhog/*` | MailHog `:8025` | Dev SMTP capture UI |

The UI service is exposed via a Kubernetes LoadBalancer with an Azure public IP. TLS termination and custom domain (`infra-advisor-ai.kyletaylor.dev`) are configured at the DNS/ingress level.
