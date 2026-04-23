---
title: Architecture
nav_order: 2
has_children: true
---

# Architecture

InfraAdvisor AI is a cloud-native microservices platform deployed on Azure Kubernetes Service. Six purpose-built services work together to ingest government data, reason over it with a multi-agent LLM pipeline, and deliver cited answers through a consultant-facing web interface.

## Service responsibilities

| Service | Role | Language | Port |
|---------|------|----------|------|
| [MCP Server](../services/mcp-server) | Data access layer — 11 tools over Model Context Protocol | Python 3.12 | 8000 |
| [Agent API](../services/agent-api) | Multi-agent reasoning, session memory, eval loop | Python 3.12 | 8001 |
| [Auth API](../services/auth-api) | User registration, JWT auth, password reset | Python 3.12 | 8002 |
| [UI](../services/ui) | React SPA — chat interface, RUM, session replay | TypeScript | 80 |
| [Load Generator](../services/load-generator) | Synthetic query traffic via Kafka | Python 3.12 | CronJob |
| [Airflow DAGs](../data-pipeline/) | Data ingestion — 5 pipelines into Azure AI Search | Python 3.12 | StatefulSet |

## Design principles

**Separation of data access from reasoning.** The MCP Server handles all external API calls and returns structured data. The Agent API handles all LLM reasoning. Neither trespasses into the other's domain.

**Observability as a first-class concern.** Every layer emits Datadog telemetry — APM spans, LLM Observability traces, RUM events, DSM Kafka metrics, DJM pipeline runs. Traces link across services via distributed tracing, and RUM sessions link to backend LLM spans.

**Stateless services, stateful infrastructure.** The MCP Server, Agent API, and Auth API are all horizontally scalable stateless pods. State lives in Redis (session memory), PostgreSQL (user accounts), Azure AI Search (knowledge base), and Azure Blob Storage (raw data).

**Fail informatively.** MCP tools return structured error dicts (never exceptions) with `retriable` flags so the LLM agent can reason about failures rather than crash.

## Sections in this chapter

- [System Overview](overview) — Service map, namespace layout, inter-service DNS, container images, nginx routing
- [Data Flow](data-flow) — Complete query lifecycle, ingestion pipeline, eval loop, Redis key schema
- [Azure Infrastructure](infrastructure) — Bicep modules, Azure resource specs, storage paths, deployment order
