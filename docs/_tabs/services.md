---
title: Services
icon: fas fa-cubes
order: 3
permalink: /services/
---

InfraAdvisor AI runs seven microservices plus an Airflow ingestion workload, all containerized and deployed on AKS. This section documents each service's API, design decisions, and observability instrumentation.

The platform offers **parallel Python and .NET implementations** of the core reasoning stack (Agent API + MCP Server). The UI lets users switch between them at runtime; both share the same PostgreSQL conversation store and Redis session memory.

## Service map

```
Browser
  │
  └── nginx (UI pod, port 80)
        ├── /auth/*        → Auth API          (port 8002)
        │                       └── PostgreSQL
        ├── /api/*         → Agent API (Python) (port 8001)
        │                       ├── Redis (session memory + suggestion pool)
        │                       ├── Kafka (eval events)
        │                       ├── PostgreSQL (conversation history)
        │                       └── MCP Server (port 8000)
        │                             └── External APIs (FHWA, FEMA, EIA, EPA, SAM.gov, …)
        ├── /api-dotnet/*  → Agent API (.NET)   (port 8001)
        │                       ├── Redis (session memory)
        │                       ├── Kafka (eval events)
        │                       ├── PostgreSQL (conversation history)
        │                       └── MCP Server .NET (port 8000)
        │                             └── External APIs (same as Python MCP)
        ├── /airflow/*     → Airflow API Server (airflow namespace)
        └── /mailhog/*     → MailHog (dev SMTP capture)
```

## At a glance

### MCP Server (Python)
The **data access layer**. Exposes 11 tools over the [Model Context Protocol](https://modelcontextprotocol.io/) HTTP transport. Fetches, normalizes, and returns structured data from government APIs and Azure AI Search. No LLM reasoning — only deterministic data retrieval. Emits custom Datadog metrics on every tool call.

### MCP Server (.NET)
A **full .NET 10 port** of the Python MCP Server. Same 11 tools, same API contract, same Scriban document templates. Uses `ModelContextProtocol.AspNetCore` and sends traces via OpenTelemetry OTLP to the Datadog Agent.

### Agent API (Python)
The **reasoning core**. Receives natural-language queries, routes them through a router LLM to select a specialist agent (engineering, water/energy, business development, document drafting, or general), executes MCP tool calls via LangChain ReAct + LangGraph, and synthesizes cited answers. Maintains 24-hour Redis session memory. Produces full LLM Observability span trees in Datadog. Persists conversation history to PostgreSQL when `DATABASE_URL` is set.

### Agent API (.NET)
A **full ASP.NET Core 10 port** of the Python Agent API. Same multi-agent routing, same 9 endpoints, same Redis memory model. Uses OpenInference.NET for LLM telemetry and exports traces via OTel OTLP. Conversation history persisted to PostgreSQL via Npgsql.

### Auth API
The **identity layer**. Handles user registration (restricted to `@datadoghq.com` by default), JWT issuance, password reset via SMTP, and admin user management. Backed by PostgreSQL with DDM-instrumented queries for Datadog Database Monitoring.

### UI
The **consultant interface**. A React 18 SPA with a conversational chat interface, citations, **backend switcher (Python / .NET)**, **model selection with localStorage persistence**, **conversation history sidebar**, feedback buttons, an MCP tool sandbox, and an admin panel. Fully instrumented with Datadog RUM + session replay.

### Load Generator
A **Kubernetes CronJob** (every 5 minutes) that samples from three YAML query corpora (happy path, edge cases, adversarial) and publishes synthetic queries to Kafka. The Agent API consumer processes them through the full pipeline, producing continuous LLM Obs traces and faithfulness scores without requiring real user traffic.

## Sections in this chapter

- [MCP Server](mcp-server) — Python: all 11 tools, parameters, return fields, custom metrics
- [MCP Server (.NET)](mcp-server-dotnet) — .NET port: tool coverage, OTel wiring, Scriban templates
- [Agent API](agent-api) — Python: multi-agent design, endpoints, session memory, conversation history, LLM Obs span tree
- [Agent API (.NET)](agent-api-dotnet) — .NET port: OTel tracing, Npgsql conversation persistence, endpoint reference
- [Auth API](auth-api) — Registration, JWT, password reset flow, DB schema, MailHog
- [Load Generator](load-generator) — Corpus structure, Kafka message format, observability
- [UI](ui) — Backend switcher, conversation sidebar, model persistence, RUM, nginx proxy
