---
title: MCP Server
parent: Services
nav_order: 1
---

# MCP Server

**Port:** 8000 | **Protocol:** Model Context Protocol (HTTP) | **Replicas:** 2

The MCP Server is the data access layer for InfraAdvisor AI. It exposes 11 tools over the [Model Context Protocol](https://modelcontextprotocol.io/) HTTP transport. The Agent API calls these tools on behalf of the LLM; no reasoning happens here — only data fetching, transformation, and document templating.

All external API calls are instrumented with Datadog APM spans and custom metrics.

## Tools

### `get_bridge_condition`

Fetches bridge inspection records from the [FHWA National Bridge Inventory](https://www.fhwa.dot.gov/bridge/nbi.cfm) via ArcGIS REST FeatureServer.

**Parameters:**
- `state` (required) — Two-letter state abbreviation (`TX`, `CA`, etc.)
- `county` — County name filter (partial match)
- `max_sufficiency` — Upper bound on sufficiency rating (0–100)
- `structural_condition` — Minimum deck/superstructure/substructure condition (0–9)
- `limit` — Max records (default 100)

**Returns:** List of bridge objects with:
- Structure number, facility, location, county
- Year built, Average Daily Traffic (ADT)
- Deck / superstructure / substructure condition codes (0–9) with human-readable labels
- Scour-critical flag, structurally deficient flag
- Sufficiency rating (0–100)
- Last inspection date, latitude/longitude

**Key field names** (exact FHWA schema, do not rename):
`STRUCTURE_NUMBER_008`, `FACILITY_CARRIED_007`, `STATE_CODE_001`, `COUNTY_CODE_003`,
`SUFFICIENCY_RATING`, `DECK_COND_058`, `SUPERSTRUCTURE_COND_059`, `SUBSTRUCTURE_COND_060`

---

### `get_disaster_history`

Fetches federal disaster declarations from [OpenFEMA](https://www.fema.gov/about/reports-and-data/openfema).

**Parameters:**
- `state` — Two-letter state abbreviation
- `incident_type` — Flood, Hurricane, Tornado, Wildfire, Winter Storm, etc.
- `start_date` / `end_date` — ISO date strings
- `declaration_type` — DR (major), EM (emergency), FM (fire)
- `limit` — Max records (default 100)

**Returns:** List of disaster declarations with:
- Disaster number, title, state, county/area
- Incident type, declaration type
- Declaration date, incident begin/end dates, closeout date
- Program declarations (IHP, PA, HM, etc.)

---

### `get_energy_infrastructure`

Fetches state electricity generation and capacity data from the [EIA API v2](https://www.eia.gov/opendata/).

**Parameters:**
- `state` — Two-letter state abbreviation
- `fuel_type` — COL (coal), NG (natural gas), NUC (nuclear), SUN (solar), WND (wind), WAT (hydro), GEO (geothermal), OTH (other)
- `year_start` / `year_end` — Integer year range
- `limit` — Max records

**Returns:** Aggregated generation (MWh) and capacity (MW) by state, fuel type, and year.

**Note:** EIA data covers generation/capacity metrics only. Cost, plant age, investment, and vulnerability data are not available through this tool.

---

### `get_water_infrastructure`

Fetches water infrastructure data from two sources:
1. **EPA SDWIS** — [Safe Drinking Water Information System](https://enviro.epa.gov/enviro/efservice) (community water systems, violations)
2. **TWDB 2026 State Water Plan** — [Texas Water Development Board](https://www.twdb.texas.gov/waterplanning/data/rwp-database/index.asp) (water supply projects)

**Parameters:**
- `state` — Two-letter state abbreviation
- `pws_type` — CWS (community), NTNCWS (non-transient non-community), TNCWS (transient)
- `source_type` — GW (groundwater), SW (surface water), GU (groundwater under influence)
- `query` — Keyword filter on system name or project description

**Returns:** Water system records with compliance status, population served, and violation history; TWDB project records with cost estimates (by decade 2030–2080), supply volume, and project type.

---

### `get_ercot_energy_storage`

Fetches Texas grid energy storage data from the [ERCOT public API](https://www.ercot.com/gridinfo/resource).

**Parameters:**
- `start_time` / `end_time` — ISO datetime strings (data is at 4-second intervals)
- `resource_name` — Filter by battery/storage resource name

**Returns:** Energy storage charge/discharge timeseries with MW values at 4-second resolution.

**Coverage:** Texas only (ERCOT grid).

---

### `search_txdot_open_data`

Searches the [TxDOT Open Data portal](https://gis-txdot.opendata.arcgis.com/) (ArcGIS Hub).

**Parameters:**
- `query` — Keyword search
- `dataset_type` — traffic_counts, construction_projects, pavement, crashes, bridges
- `limit` — Max results

**Returns:** Dataset records from TxDOT GIS portal with AADT (Annual Average Daily Traffic), construction project status, and other transport metrics.

**Coverage:** Texas only.

---

### `get_procurement_opportunities`

Searches for active federal contract and grant opportunities from:
1. **SAM.gov** — Federal contract solicitations (`api.sam.gov/opportunities/v2/search`)
2. **grants.gov** — Federal grant opportunities (EPA CWSRF, DWSRF, FEMA BRIC, RAISE, CDBG-DR, USACE, EDA)

**Parameters:**
- `query` — Keywords (NAICS code derived automatically from query)
- `state` — Two-letter state abbreviation
- `agency` — Agency acronym filter
- `set_aside` — Small business set-aside type (SBA, 8A, WOSB, HUBZ, SDVOSB)

**Returns:** Merged list sorted by deadline, with source flag (`SAM.gov` or `grants.gov`), solicitation number, title, agency, NAICS, response deadline, and set-aside type.

**Date range:** Last 364 days (SAM.gov limit). Range is noted in `_note` field when clamped.

---

### `get_contract_awards`

Searches historical federal contract awards from [USASpending.gov](https://www.usaspending.gov/).

**Parameters:**
- `query` — Keywords (NAICS derived automatically)
- `state` — Filter by place of performance state
- `agency_names` — List of agency name filters
- `min_award_usd` — Minimum award amount in dollars
- `limit` — Max records

**Returns:** Award records with recipient, amount, agency, contract description, location, NAICS code, and award date.

**Use:** Competitive intelligence — see which firms are winning infrastructure contracts.

---

### `search_web_procurement`

Searches government websites for state/local RFPs, bond election information, and procurement notices via the [Tavily Search API](https://tavily.com/).

**Parameters:**
- `query` — Search query
- `state` — Focus on specific state
- `limit` — Max results

**Returns:** Extracted procurement records from `.gov`, `.us`, DemandStar, BidNet, and BonfireHub pages. Each record has title, description, agency, deadline, and source URL where available.

**Requires:** `TAVILY_API_KEY` environment variable.

---

### `search_project_knowledge`

Hybrid semantic + BM25 search against the Azure AI Search knowledge base populated by the Airflow pipelines.

**Parameters:**
- `query` — Natural language or keyword search
- `domain` — Filter by domain (transportation, water, energy, environmental, business_development)
- `document_type` — Filter by doc type
- `top` — Number of results (default 5)

**Returns:** Ranked document chunks with content, source, domain, and relevance score.

**Index:** `infra-advisor-knowledge` — populated by all 5 Airflow DAGs plus the knowledge base init pipeline.

---

### `draft_document`

Generates structured document scaffolds using Jinja2 templates. No LLM is invoked — this tool produces deterministic template output.

**Parameters:**
- `document_type` — `statement_of_work`, `risk_summary`, `cost_estimate`, `funding_memo`
- `context` — Dict of values to inject into template fields

**Returns:** Rendered document text (Markdown) for the specified document type.

**Templates:**
- `statement_of_work` — SOW with scope, deliverables, timeline, exclusions
- `risk_summary` — Risk register with likelihood, impact, mitigation
- `cost_estimate` — Line-item cost table with totals
- `funding_memo` — Federal funding opportunity summary with eligibility and match requirements

---

## Health endpoint

```
GET /health
```

Returns service status and API key configuration:

```json
{
  "status": "ok",
  "tools": ["get_bridge_condition", "get_disaster_history", ...],
  "keys_configured": {
    "samgov": true,
    "tavily": false
  }
}
```

## Observability

**Custom Datadog metrics (emitted on every tool call):**

| Metric | Tags | Description |
|--------|------|-------------|
| `mcp.tool.calls` | `tool`, `status` (success/error) | Tool invocation count |
| `mcp.tool.latency_ms` | `tool` | End-to-end tool execution time |
| `mcp.external_api.latency_ms` | `source` (arcgis_nbi, openfema, eia, etc.) | Upstream API response time |
| `mcp.external_api.errors` | `source`, `error_type` | Upstream API error count |

**APM:** `ddtrace.auto` instruments all outbound HTTP requests via `httpx`. Spans appear in Datadog APM under service `mcp-server`.

## Error handling

Each tool returns structured errors rather than raising exceptions, allowing the LLM to reason about failures:

```python
{
  "error": "Azure AI Search index 'infra-advisor-knowledge' not found",
  "action": "Run knowledge_base_init Airflow DAG (make run-dags) to initialize the index",
  "retriable": false
}
```

The `retriable: false` flag tells the Agent API's LangChain executor not to retry, preventing endless tool call loops.
