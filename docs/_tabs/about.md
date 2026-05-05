---
title: About
icon: fas fa-info-circle
order: 1
---

# InfraAdvisor AI

**Multi-agent AI platform for infrastructure consulting firms.**

InfraAdvisor AI combines government data pipelines, LangChain ReAct reasoning, and full-stack Datadog observability on Azure Kubernetes Service. It provides a consultant-facing chat interface backed by real US government datasets — bridge conditions, disaster history, energy infrastructure, water systems, and federal procurement data.

## What it does

Infrastructure consultants interact with the platform through a conversational interface. A router LLM classifies each query by domain, then hands off to a specialist agent equipped with the right subset of MCP tools. The specialist runs a ReAct loop — reasoning, tool calls, more reasoning — until it can produce a cited, structured answer.

The same platform ships in two parallel implementations: a Python stack (FastAPI + LangChain + ddtrace) and a .NET stack (ASP.NET Core 10 + OpenTelemetry). The UI's backend switcher lets you compare them in real time.

## Data sources

| Source | Data | Update frequency |
|--------|------|-----------------|
| [FHWA NBI](https://www.fhwa.dot.gov/bridge/nbi.cfm) | 615k+ bridge inspection records | Weekly |
| [OpenFEMA](https://www.fema.gov/about/reports-and-data/openfema) | Federal disaster declarations | Daily |
| [EIA API v2](https://www.eia.gov/opendata/) | State electricity generation & capacity | Weekly |
| [EPA SDWIS](https://enviro.epa.gov/enviro/efservice) | Community water systems | Monthly |
| [TWDB](https://www.twdb.texas.gov/) | Texas 2026 State Water Plan | Monthly |
| [SAM.gov](https://sam.gov/) | Federal contract solicitations | On-demand |
| [USASpending.gov](https://www.usaspending.gov/) | Historical contract awards | On-demand |
| [Tavily](https://tavily.com/) | Web search (state/local RFPs) | On-demand |
| [ERCOT](https://www.ercot.com/gridinfo/resource) | Texas grid energy storage | On-demand |

## Observability stack

Every layer emits Datadog telemetry — APM traces, LLM Observability span trees, RUM session replay, Data Jobs Monitoring, Data Streams Monitoring, and Database Monitoring. Traces link end-to-end from browser click through LLM reasoning to database query.

## Source

[github.com/kyletaylored/infra-advisor-ai](https://github.com/kyletaylored/infra-advisor-ai)
