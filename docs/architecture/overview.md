---
title: System Overview
parent: Architecture
nav_order: 1
---

# System Overview

## Service map

Six microservices work together to serve infrastructure advisory queries. Each runs as a containerized workload on AKS, with images published to GitHub Container Registry.

```
┌─────────────────────────────────────────────────────────────────────┐
│  Browser                                                             │
│  React 18 + Chakra UI + Datadog RUM                                  │
│  https://infra-advisor-ai.kyletaylor.dev                             │
└────────────┬──────────────────────────┬────────────────────────────┘
             │ /auth/*                  │ /api/*
             ▼                          ▼
┌────────────────────┐    ┌─────────────────────────────────────────┐
│   Auth API         │    │   Agent API                             │
│   :8002            │    │   :8001                                 │
│   FastAPI          │    │   FastAPI + LangChain ReAct + LangGraph │
│   PostgreSQL ORM   │    │   Redis session memory                  │
│   JWT + bcrypt     │    │   Kafka eval producer                   │
└────────────────────┘    └──────────────────┬────────────────────┘
         │                                   │ MCP HTTP
         │                                   ▼
         │                    ┌──────────────────────────┐
         │                    │   MCP Server             │
         │                    │   :8000                  │
         │                    │   FastMCP                │
         │                    │   11 data tools          │
         │                    └─────────┬────────────────┘
         │                              │
         ▼                   ┌──────────┴─────────┐
┌─────────────────┐          │  External APIs      │
│  PostgreSQL     │          │  FHWA NBI ArcGIS    │
│  :5432          │          │  OpenFEMA REST      │
│  User accounts  │          │  EIA API v2         │
└─────────────────┘          │  EPA SDWIS          │
                             │  ERCOT public API   │
                             │  TxDOT Open Data    │
                             │  SAM.gov            │
                             │  USASpending.gov    │
                             │  Tavily search      │
                             └────────────────────┘

┌────────────────────────────────────────────────────────────────────┐
│  Data Pipeline (Airflow)                                           │
│  Scheduler StatefulSet — LocalExecutor — 5 DAGs                   │
│  NBI → FEMA → EIA → TWDB/EPA → Knowledge Base                     │
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
| `infra-advisor` | mcp-server, agent-api, auth-api, ui, redis, postgres, mailhog, load-generator |
| `airflow` | Airflow scheduler, API server, dag-processor, triggerer, PostgreSQL |
| `kafka` | Strimzi Operator, Kafka cluster, topics |
| `datadog` | Datadog Agent DaemonSet, Cluster Agent |

## Inter-service communication

All service-to-service traffic uses Kubernetes DNS names (`<service>.<namespace>.svc.cluster.local`):

| From | To | Protocol | DNS Name |
|------|----|----------|----------|
| nginx (UI) | Agent API | HTTP | `agent-api.infra-advisor.svc.cluster.local:8001` |
| nginx (UI) | Auth API | HTTP | `auth-api.infra-advisor.svc.cluster.local:8002` |
| Agent API | MCP Server | HTTP (MCP) | `mcp-server.infra-advisor.svc.cluster.local:8000` |
| Agent API | Redis | Redis protocol | `redis.infra-advisor.svc.cluster.local:6379` |
| Agent API | Kafka | Kafka protocol | `kafka-cluster-kafka-bootstrap.kafka.svc.cluster.local:9092` |
| Load Generator | Kafka | Kafka protocol | `kafka-cluster-kafka-bootstrap.kafka.svc.cluster.local:9092` |
| Airflow | Azure OpenAI | HTTPS | `*.openai.azure.com` |
| Airflow | Azure AI Search | HTTPS | `*.search.windows.net` |
| Airflow | Azure Blob Storage | HTTPS | `*.blob.core.windows.net` |
| All pods | Datadog Agent | UDP/TCP | `datadog-agent.datadog.svc.cluster.local:8125/8126` |

## Container images

All images are hosted at `ghcr.io/kyletaylored/infra-advisor-ai/<service>:latest` and built by GitHub Actions on every merge to `main`.

| Service | Image | Language | Base |
|---------|-------|----------|------|
| MCP Server | `mcp-server` | Python 3.12 | `python:3.12-slim` |
| Agent API | `agent-api` | Python 3.12 | `python:3.12-slim` |
| Auth API | `auth-api` | Python 3.12 | `python:3.12-slim` |
| Load Generator | `load-generator` | Python 3.12 | `python:3.12-slim` |
| UI | `ui` | TypeScript/nginx | `node:20-alpine` → `nginx:alpine` |

## Ingress

A single nginx Deployment (in the `ui` pod) acts as the ingress reverse proxy for the entire application:

| Path prefix | Proxied to | Notes |
|-------------|-----------|-------|
| `/` | nginx static files | React SPA (`/assets/`) |
| `/api/*` | Agent API `:8001` | Strips `/api/` prefix |
| `/auth/*` | Auth API `:8002` | Strips `/auth/` prefix |
| `/airflow/*` | Airflow API Server `:8080` | Preserves `/airflow/` prefix |
| `/mailhog/*` | MailHog `:8025` | Dev SMTP capture UI |

The UI service is exposed via a Kubernetes LoadBalancer with an Azure public IP. TLS termination and custom domain (`infra-advisor-ai.kyletaylor.dev`) are configured at the DNS/ingress level.
