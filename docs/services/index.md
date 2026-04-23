---
title: Services
nav_order: 3
has_children: true
---

# Services

InfraAdvisor AI runs five microservices plus an Airflow ingestion workload, all containerized and deployed on AKS. This section documents each service's API, design decisions, and observability instrumentation.

## Service map

```
Browser
  │
  └── nginx (UI pod, port 80)
        ├── /auth/*  → Auth API (port 8002)
        │                └── PostgreSQL
        ├── /api/*   → Agent API (port 8001)
        │                ├── Redis (session memory + suggestion pool)
        │                ├── Kafka (eval events)
        │                └── MCP Server (port 8000)
        │                      └── External APIs (FHWA, FEMA, EIA, EPA, SAM.gov, …)
        ├── /airflow/* → Airflow API Server (airflow namespace)
        └── /mailhog/* → MailHog (dev SMTP capture)
```

## At a glance

### MCP Server
The **data access layer**. Exposes 11 tools over the [Model Context Protocol](https://modelcontextprotocol.io/) HTTP transport. Fetches, normalizes, and returns structured data from government APIs and Azure AI Search. No LLM reasoning — only deterministic data retrieval. Emits custom Datadog metrics on every tool call.

### Agent API
The **reasoning core**. Receives natural-language queries, routes them through a router LLM to select a specialist agent (engineering, water/energy, business development, document drafting, or general), executes MCP tool calls via LangChain ReAct + LangGraph, and synthesizes cited answers. Maintains 24-hour Redis session memory. Produces full LLM Observability span trees in Datadog.

### Auth API
The **identity layer**. Handles user registration (restricted to `@datadoghq.com` by default), JWT issuance, password reset via SMTP, and admin user management. Backed by PostgreSQL with DDM-instrumented queries for Datadog Database Monitoring.

### UI
The **consultant interface**. A React 18 SPA that provides a conversational chat interface with citations, model selection, session persistence, feedback buttons, an MCP tool sandbox, and an admin panel. Fully instrumented with Datadog RUM + session replay. RUM session IDs are threaded through to backend LLM Obs spans.

### Load Generator
A **Kubernetes CronJob** (every 5 minutes) that samples from three YAML query corpora (happy path, edge cases, adversarial) and publishes synthetic queries to Kafka. The Agent API consumer processes them through the full pipeline, producing continuous LLM Obs traces and faithfulness scores without requiring real user traffic.

## Sections in this chapter

- [MCP Server](mcp-server) — All 11 tools: parameters, return fields, error handling, custom metrics
- [Agent API](agent-api) — Multi-agent design, endpoints, session memory, Kafka integration, LLM Obs span tree
- [Auth API](auth-api) — Registration, JWT, password reset flow, DB schema, MailHog
- [Load Generator](load-generator) — Corpus structure, Kafka message format, observability
- [UI](ui) — Features, component tree, Datadog RUM, session-trace linking, nginx proxy
