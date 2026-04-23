---
title: Home
nav_order: 1
description: InfraAdvisor AI documentation home
permalink: /
---

# InfraAdvisor AI

**Infrastructure advisory at your fingertips.** InfraAdvisor AI is a production-grade, fully observable AI agent platform for architecture, engineering, construction, operations, and management (AECOM) consulting firms.

---

## What it does

InfraAdvisor AI lets infrastructure consultants ask natural-language questions against real government datasets — bridge conditions, disaster history, energy capacity, water infrastructure, and federal procurement — and receive cited, synthesized answers in seconds.

```
"What are the lowest-rated bridges in Harris County, TX, by sufficiency score?"

→ Calls FHWA NBI → ranks 18 records → returns structured table with citations
```

Key capabilities:

- **Multi-agent reasoning:** A router LLM routes each query to the appropriate domain specialist (engineering, water/energy, business development, document drafting, or general). Each specialist works with a curated subset of tools to reduce hallucination surface.
- **Live government data:** 5 Airflow pipelines ingest 615k+ records weekly from FHWA, FEMA, EIA, EPA, and TWDB into a hybrid vector + BM25 search index.
- **Full-stack observability:** Every layer instrumented with Datadog — APM, LLM Observability, RUM session replay, Data Streams Monitoring, Data Jobs Monitoring, and CSPM.
- **Session memory:** Redis-backed conversation history persists across page reloads (24-hour TTL).

---

## Architecture at a glance

```
Browser (React + Datadog RUM)
  │  JWT auth  │  /api/query
  ▼            ▼
Auth API      Agent API ──── Redis (session memory)
(Postgres)     │
               │  MCP HTTP
               ▼
          MCP Server (11 tools)
          ├── FHWA NBI ArcGIS
          ├── OpenFEMA REST
          ├── EIA API v2
          ├── EPA SDWIS
          ├── ERCOT public API
          ├── TxDOT Open Data
          ├── SAM.gov + grants.gov
          ├── USASpending.gov
          ├── Tavily web search
          ├── Azure AI Search (knowledge base)
          └── Jinja2 document templates

Airflow (5 DAGs) ──► Azure Blob Storage ──► Azure AI Search

Load Generator ──► Kafka ──► Agent API (eval loop)

All pods ──► Datadog Agent (APM + logs + metrics + LLM Obs + DJM + DSM)
```

---

## Quick navigation

| Section | Description |
|---------|-------------|
| [Architecture](architecture/) | System design, service map, data flow |
| [Services](services/) | API reference for each microservice |
| [Data Pipeline](data-pipeline/) | Airflow DAGs and ingestion details |
| [Observability](observability/) | Datadog instrumentation across every layer |
| [Deployment](deployment/) | Prerequisites, secrets, step-by-step deploy |
| [Development](development/) | Local setup, testing, conventions |

---

## Technology stack

| Layer | Technology |
|-------|-----------|
| Cloud | Azure (AKS, OpenAI, AI Search, Blob Storage) |
| Orchestration | Kubernetes 1.30 on AKS — 3× Standard_D2s_v3 nodes |
| LLM inference | Azure OpenAI (gpt-4.1-mini, gpt-4.1) |
| Embeddings | Azure OpenAI text-embedding-3-small |
| Knowledge base | Azure AI Search (hybrid vector + BM25) |
| Agent framework | LangChain ReAct + LangGraph |
| Tool protocol | Model Context Protocol (MCP) over HTTP |
| Session memory | Redis (K8s Deployment) |
| Data pipeline | Apache Airflow 3.x (LocalExecutor, Helm chart) |
| Message bus | Apache Kafka (Strimzi Operator on AKS) |
| Observability | Datadog (APM, LLM Obs, RUM, DJM, DSM, CSPM) |
| Auth | FastAPI + PostgreSQL + JWT + bcrypt |
| Frontend | React 18 + Chakra UI v3 + Vite |
| IaC | Azure Bicep + Helm + kubectl manifests |
| CI/CD | GitHub Actions → GHCR → AKS rolling deploy |

---

## Live environment

| Resource | URL |
|----------|-----|
| Application | `https://infra-advisor-ai.kyletaylor.dev` |
| Airflow UI | `https://infra-advisor-ai.kyletaylor.dev/airflow` |
| MailHog (dev) | `https://infra-advisor-ai.kyletaylor.dev/mailhog` |
| Azure resource group | `rg-tola-infra-advisor-ai` |
| Container registry | `ghcr.io/kyletaylored/infra-advisor-ai` |
