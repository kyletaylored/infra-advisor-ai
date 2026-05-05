---
title: MCP Server (.NET)
parent: Services
nav_order: 2
---

# MCP Server (.NET)

**Port:** 8000 | **Framework:** ModelContextProtocol.AspNetCore 0.2.0-preview | **Replicas:** 2

A full .NET 10 port of the [Python MCP Server](mcp-server). Exposes the same 11 tools over the Model Context Protocol HTTP transport. Exclusively used by the `.NET Agent API` (`agent-api-dotnet`) — the Python Agent API continues to use the Python MCP Server. The two stacks are fully isolated with no cross-traffic.

## Tools

All 11 tools are implemented identically to the Python version:

| Tool | Description |
|------|-------------|
| `get_bridge_condition` | FHWA NBI bridge structural data via ArcGIS REST |
| `get_disaster_history` | OpenFEMA declared disaster history |
| `get_energy_infrastructure` | EIA energy infrastructure data |
| `get_water_infrastructure` | EPA SDWIS / TWDB water system data |
| `get_ercot_energy_storage` | ERCOT real-time energy storage capacity |
| `search_txdot_open_data` | TxDOT open data portal search |
| `search_project_knowledge` | Azure AI Search vector + keyword search |
| `get_procurement_opportunities` | SAM.gov contract opportunities |
| `get_contract_awards` | USASpending.gov contract awards |
| `search_web_procurement` | Tavily web search for procurement context |
| `draft_document` | Generate infrastructure documents from Scriban templates |

## Document templates

`draft_document` renders one of four Scriban templates embedded in the assembly:

| Template key | Output |
|-------------|--------|
| `cost_estimate` | Cost estimate scaffold |
| `funding_memo` | Funding positioning memo |
| `risk_summary` | Risk summary report |
| `scope_of_work` | Statement of work |

Templates live in `services/mcp-server-dotnet/Templates/` and are converted from the Python Jinja2 originals.

## Observability

**Tracing:** OpenTelemetry OTLP HTTP to `datadog-agent.datadog.svc.cluster.local:4318`. No ddtrace — the .NET MCP Server uses only OTel.

| Span | Instrumented by |
|------|----------------|
| HTTP requests (`POST /mcp`, `GET /health`) | `AddAspNetCoreInstrumentation` (auto) |
| Outbound HTTP (government APIs, Tavily) | `AddHttpClientInstrumentation` (auto) |
| Azure AI Search calls | `AddHttpClientInstrumentation` (auto, via REST) |

**Service name:** `infratools-mcp-dotnet`

## Routing isolation

| Service | MCP_SERVER_URL |
|---------|----------------|
| `agent-api` (Python) | `http://mcp-server.infra-advisor.svc.cluster.local:8000/mcp` |
| `agent-api-dotnet` (.NET) | `http://mcp-server-dotnet.infra-advisor.svc.cluster.local:8000/mcp` |

## Build and run locally

```bash
cd services/mcp-server-dotnet
dotnet restore
dotnet build

AZURE_OPENAI_ENDPOINT=https://... \
AZURE_OPENAI_API_KEY=... \
AZURE_SEARCH_ENDPOINT=https://... \
AZURE_SEARCH_API_KEY=... \
EIA_API_KEY=... \
dotnet run --urls http://localhost:8004
```

Health check:
```bash
curl http://localhost:8004/health
```

## Required secrets

Create with `make create-mcp-server-dotnet-secret` (or `make create-secrets`):

| Variable | Description |
|----------|-------------|
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI resource URL |
| `AZURE_OPENAI_API_KEY` | Azure OpenAI API key |
| `AZURE_SEARCH_ENDPOINT` | Azure AI Search endpoint |
| `AZURE_SEARCH_API_KEY` | Azure AI Search admin key |
| `EIA_API_KEY` | U.S. EIA Open Data API key |
| `ERCOT_API_KEY` | ERCOT API key (optional — tool skips if unset) |
| `SAMGOV_API_KEY` | SAM.gov API key (optional) |
| `TAVILY_API_KEY` | Tavily search API key (optional) |
