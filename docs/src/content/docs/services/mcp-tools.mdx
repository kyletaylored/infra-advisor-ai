---
title: MCP Tool Reference
description: What each MCP tool does, when to use it, and how to invoke it correctly
---

import { Aside } from '@astrojs/starlight/components';

The MCP server exposes 11 tools that the agent can invoke during a conversation. This page documents each one — its purpose, data scope, when to reach for it (and when not to), required arguments with realistic examples, and common gotchas.

The tool descriptions baked into the agent's tool catalog mirror the content here, so this page also doubles as a debugging reference: if the agent picks the wrong tool, the "When NOT to use" section tells you what description to tighten.

<Aside type="tip">
**For a single-page overview**, jump to the [Decision matrix](#decision-matrix). For chained-tool patterns, see [Chained patterns](#chained-patterns). For the reference data the agent fills into tool args, see [Reference data](#reference-data).
</Aside>

## Decision matrix

If the user asks about… | Use this tool | Notes
---|---|---
Bridge ratings / structural deficiency / scour | [`get_bridge_condition`](#get_bridge_condition) | Nationwide. 2-char FIPS state code.
Federal disaster declarations / hurricane / flood history | [`get_disaster_history`](#get_disaster_history) | Nationwide.
Electricity generation, capacity, fuel mix by state | [`get_energy_infrastructure`](#get_energy_infrastructure) | Annual data, all 50 states.
ERCOT grid storage (Texas only) | [`get_ercot_energy_storage`](#get_ercot_energy_storage) | Texas only.
Drinking-water systems / SDWA violations | [`get_water_infrastructure`](#get_water_infrastructure) | `query_type=violations` or `water_systems`.
TWDB recommended water plan projects (Texas only) | [`get_water_infrastructure`](#get_water_infrastructure) | `query_type=water_plan_projects`.
Texas highway data (AADT, construction projects) | [`search_txdot_open_data`](#search_txdot_open_data) | Texas only.
Firm precedent / SOW templates / case studies | [`search_project_knowledge`](#search_project_knowledge) | Always call BEFORE `draft_document`.
**Open** federal RFPs / grants | [`get_procurement_opportunities`](#get_procurement_opportunities) | Active solicitations.
**Past** federal contract awards | [`get_contract_awards`](#get_contract_awards) | Always call BEFORE `get_procurement_opportunities` for BD.
State / local procurement (.gov sites) | [`search_web_procurement`](#search_web_procurement) | Azure web search.
Generate SOW / risk summary / cost estimate | [`draft_document`](#draft_document) | Always chain after `search_project_knowledge`.

---

## get_bridge_condition

**Purpose:** Query the FHWA National Bridge Inventory (NBI) for bridges matching specified criteria. Returns structure-level condition data: ratings (0-9 scale), `BRIDGE_CONDITION` (Good / Fair / Poor), scour-critical flag, ADT, year built, location.

**Coverage:** All US states + DC + Puerto Rico. ~617,000 public bridges over 20 ft span. Refreshed annually by FHWA.

**When to use:**
- "Structurally deficient bridges in `<state>`"
- "Bridges with sufficiency rating under N"
- "High-traffic bridges needing inspection in `<county>`"
- "Scour-vulnerable bridges along a flood corridor"
- "Oldest bridges in an area"

**When NOT to use:**
- Rail bridges (NBI is highway only)
- Culverts under 20 ft span
- Real-time inspection findings (data is annual)
- Pedestrian-only bridges

**Required arg:** `state_code` — **2-character FIPS code with leading zero**, NOT a 2-letter abbreviation.

```jsonc
get_bridge_condition({
  "state_code": "48",                  // Texas, NOT "TX"
  "county_code": "201",                // Harris County (optional)
  "max_lowest_rating": 4,              // 0-9, 4 = poor and below
  "structurally_deficient_only": true, // FHWA's official classification
  "min_adt": 10000,                    // Only major-highway-volume bridges
  "limit": 25                          // 1-200, default 50
})
```

**Common gotchas:**
- Using `state_code: "TX"` instead of `state_code: "48"` returns no results.
- `max_lowest_rating` is inclusive — `4` includes 4, 3, 2, 1, 0.
- The `last_inspection_before` arg is documentation-only; not applied as a server-side filter.

---

## get_disaster_history

**Purpose:** Federal disaster declaration history from OpenFEMA — major-disaster, emergency, and fire-management declarations with declaration ID, incident type, dates, affected counties, and program activations.

**Coverage:** Every US state + territory, 1953 to present. No API key required.

**When to use:**
- "How often does `<county>` get hurricanes / floods / wildfires?"
- "What disasters affected the project area in the last N years?"
- "Counties with repeat flood declarations"
- "FEMA Public Assistance funding history"
- Multi-hazard exposure assessment for resilience planning

**When NOT to use:**
- Real-time or active disasters (this is historical declarations)
- Individual property damage data
- FEMA flood-zone maps (different source)
- State / local emergency declarations not in the federal record

```jsonc
get_disaster_history({
  "states": ["TX", "LA", "MS"],         // 2-letter abbreviations
  "incident_types": ["Hurricane", "Flood"],
  "date_from": "2014-01-01",
  "date_to": "2024-12-31",
  "limit": 100
})
```

**Common `incident_types`:** `Flood`, `Hurricane`, `Severe Storm`, `Tornado`, `Fire`, `Earthquake`, `Drought`, `Severe Ice Storm`, `Winter Storm`, `Coastal Storm`, `Tropical Storm`.

---

## get_energy_infrastructure

**Purpose:** EIA state-level annual electricity statistics — generation (MWh), capacity (MW), or fuel mix (%) broken out by fuel type.

**Coverage:** All 50 states + DC. Annual data, multi-year history available. Requires `EIA_API_KEY`.

**When to use:**
- "How much electricity does `<state>` generate by fuel?"
- "Renewable energy share by state"
- "Installed capacity for solar / wind / gas"
- "Energy mix trends over time"
- State-level resource planning context

**When NOT to use:**
- Texas ERCOT real-time grid data (use [`get_ercot_energy_storage`](#get_ercot_energy_storage))
- Individual power plants (EIA-860 plant-level not exposed here)
- Transmission or distribution data
- ENERGY STAR or efficiency programs

```jsonc
get_energy_infrastructure({
  "states": ["TX", "CA"],
  "data_series": "fuel_mix",       // "generation" | "capacity" | "fuel_mix"
  "year_from": 2019,
  "year_to": 2024,
  "fuel_types": ["SUN", "WND"]    // optional filter
})
```

**`data_series` semantics:**
- `generation` → MWh actually generated per fuel
- `capacity` → MW of nameplate generating capacity installed
- `fuel_mix` → percentage share of generation by fuel

**Common fuel codes:** `SUN` solar, `WND` wind, `NG` natural gas, `COL` coal, `NUC` nuclear, `HYC` conventional hydro, `BIO` biomass, `GEO` geothermal, `PET` petroleum.

---

## get_ercot_energy_storage

**Purpose:** ERCOT public-data API — battery / Energy Storage Resource (ESR) charging operations on the Texas grid. 4-second-interval data.

**Coverage:** Texas ERCOT footprint only (~90% of Texas — excludes El Paso area, the Panhandle SPP region, and parts of East TX). Requires `ERCOT_API_KEY`.

**When to use:**
- "How is battery storage performing on the ERCOT grid?"
- "ESR discharge during peak-demand windows"
- "Grid-scale storage MW available right now"
- "Real-time charging behaviour during stress events"

**When NOT to use:**
- States outside Texas (use [`get_energy_infrastructure`](#get_energy_infrastructure))
- The Texas Panhandle / SPP region
- Individual battery sites (use EIA-860 directly)
- ERCOT load forecasts or LMPs (different ERCOT products not exposed here)

```jsonc
get_ercot_energy_storage({
  "query_type": "charging_data",        // or "products" for catalog lookup
  "time_from": "2024-06-01T00:00:00",
  "time_to":   "2024-06-01T01:00:00",
  "min_charging_mw": 50,                 // grid-scale only
  "page": 1,
  "size": 100
})
```

---

## get_water_infrastructure

**Purpose:** Three water datasets behind one tool, dispatched by `query_type`.

**Coverage:**
- `water_systems` / `violations` → all US public water systems (EPA SDWIS)
- `water_plan_projects` → **Texas only** (TWDB 2026 State Water Plan)

**When to use:**

| `query_type` | Use for |
|---|---|
| `water_systems` | Inventory of public water systems, population served, CWS / NTNCWS / TNCWS breakdown |
| `violations` | Open SDWA violations, Tier 1 / 2 / 3, PWSIDs needing remediation |
| `water_plan_projects` | TWDB recommended Texas projects, desalination / aquifer / reuse strategies, regions A–P |

**When NOT to use:**
- Stormwater / wastewater treatment plants (try `search_project_knowledge` or web search)
- Individual water-quality test results
- Non-Texas state water plans

```jsonc
get_water_infrastructure({
  "query_type": "violations",
  "states": ["TX"],
  "system_types": ["CWS"],            // CWS = Community Water System
  "has_violations": true,
  "min_population_served": 10000,
  "limit": 50
})
```

**EPA system types:**
- `CWS` — Community Water System (residential)
- `NTNCWS` — Non-Transient Non-Community (schools, factories)
- `TNCWS` — Transient Non-Community (rest stops, parks)

**PWSID** is the 9-character Public Water System ID. Always cite PWSIDs for individual systems.

---

## search_txdot_open_data

**Purpose:** Search the TxDOT Open Data portal (ArcGIS Hub) for Texas transportation datasets — AADT counts, construction projects, highway geometry, district-level GIS layers.

**Coverage:** Texas only. No API key required.

**When to use:**
- "AADT for a specific Texas highway / road"
- "Active or planned TxDOT construction projects"
- "Traffic-count history"
- TxDOT GIS dataset discovery

**When NOT to use:**
- Federal highway data outside Texas (use [`get_bridge_condition`](#get_bridge_condition) for NBI, or that state's DOT portal)
- Non-roadway Texas data (TxDOT only — DPS, RRC, TWDB are separate)
- Real-time traffic

```jsonc
search_txdot_open_data({
  "query_type": "catalog_search",          // or "traffic_counts" / "construction_projects"
  "query": "pavement condition",            // required for catalog_search
  "county": "Harris",                       // optional
  "limit": 20
})
```

---

## search_project_knowledge

**Purpose:** Hybrid semantic + keyword search against the firm's internal Azure AI Search knowledge base — case studies, prior project SOWs, risk frameworks, document templates, vetted best practices.

**Coverage:** Whatever the ingestion pipeline has indexed. Base index ships with TWDB state water plan projects + a small set of curated templates.

**When to use:**
- "Similar projects we've done for `<work type>`"
- "SOW / risk-summary / cost-estimate templates"
- "Firm precedent for `<project type>`"
- Vetted best practices not in live data APIs

**ALWAYS** call this BEFORE [`draft_document`](#draft_document) — pulls templates + prior project context that `draft_document` needs.

**When NOT to use:**
- Live external data (use the domain-specific tools)
- Internet search (use [`search_web_procurement`](#search_web_procurement))
- Current procurement opportunities (use [`get_procurement_opportunities`](#get_procurement_opportunities))

```jsonc
search_project_knowledge({
  "query": "bridge rehabilitation SOW IH-35",
  "document_types": ["sow", "case_study"],   // optional
  "domains": ["transportation"],              // optional
  "top_k": 6
})
```

---

## draft_document

**Purpose:** Render a structured consulting deliverable from a Scriban template + supplied context. Returns Markdown ready for client review. **Deterministic** — no LLM invoked inside the tool itself.

**When to use:**
- "Draft an SOW for `<project>`"
- "Produce a risk summary"
- "Create a cost-estimate scaffold"
- "Write a funding-positioning memo"

**ALWAYS** call [`search_project_knowledge`](#search_project_knowledge) FIRST to pull templates / prior-project context, then pass the retrieved snippets into `context` here so the draft is grounded.

**When NOT to use:**
- Free-form text generation (the agent LLM is the right tool)
- Detailed cost models (this is a scaffold only)
- Documents outside the 4 supported types

```jsonc
draft_document({
  "document_type": "scope_of_work",   // | "risk_summary" | "cost_estimate_scaffold" | "funding_positioning_memo"
  "context": { "bridges": [...], "best_practices": [...] },
  "project_name": "IH-35 Bridge Rehabilitation Phase II",
  "client_name": "TxDOT Austin District",
  "notes": "Phase II only — Phase I scope already complete."
})
```

---

## get_procurement_opportunities

**Purpose:** Active / open federal opportunities — SAM.gov contract solicitations + grants.gov open grant programs. Merged result list sorted by deadline.

**Coverage:** Every currently-open federal solicitation + open grant. Requires `SAMGOV_API_KEY`. Internal default window: next 90 days.

**When to use:**
- "Open RFPs for `<work type>`"
- "Upcoming bid deadlines"
- "Active federal grant programs"
- "What's on SAM.gov right now for NAICS `<code>`?"

**Pairing rule:** For BD research, call [`get_contract_awards`](#get_contract_awards) FIRST — knowing past winners + pricing informs which open opportunities are worth pursuing.

**When NOT to use:**
- Historical awards (use [`get_contract_awards`](#get_contract_awards))
- State / local RFPs (use [`search_web_procurement`](#search_web_procurement))
- Asking the user for a date range (tool internally defaults to 90 days — **never ask**)

```jsonc
get_procurement_opportunities({
  "query": "civil engineering",
  "geography": "TX",
  "naics_codes": ["237310", "541330"],
  "min_value_usd": 1000000,
  "opportunity_types": ["contract", "grant"],
  "limit": 20
})
```

---

## get_contract_awards

**Purpose:** USASpending.gov historical federal contract awards — who won similar work, at what price, for which agencies.

**Coverage:** All federal contracts + grants ever recorded by USASpending. No API key required. Default window: past 2 years.

**When to use:**
- "Who won contracts for `<work type>`?"
- "Typical award amounts for NAICS `<code>` in `<state>`"
- "Incumbent contractors for `<agency>`"
- Competitive intel before bidding
- Pricing benchmarks for SOW drafts

**Pairing rule:** For BD queries, ALWAYS call this BEFORE [`get_procurement_opportunities`](#get_procurement_opportunities).

**When NOT to use:**
- Active / open solicitations (use [`get_procurement_opportunities`](#get_procurement_opportunities))
- State or local awards (this is federal only)
- Contracts under $25K (USASpending threshold)

```jsonc
get_contract_awards({
  "query": "bridge rehabilitation",
  "geography": "TX",
  "naics_codes": ["237310"],
  "agency_names": ["Department of Transportation"],
  "date_from": "2022-01-01",
  "min_award_usd": 500000,
  "limit": 25
})
```

---

## search_web_procurement

**Purpose:** Search government / procurement-portal websites for state and local RFPs, bond elections, and budget announcements. Uses Azure OpenAI's Responses API with the `web_search_preview` tool — single round trip runs the live search and extracts structured procurement records via JSON schema.

**Coverage:** `.gov`, `.us`, DemandStar, BidNet, BonfireHub. Uses the same `AZURE_OPENAI_API_KEY` as the rest of the AI stack — no separate vendor key.

**When to use:**
- State / local government RFPs (federal goes through `get_procurement_opportunities`)
- Bond elections and municipal bond initiatives
- Infrastructure budget / capital improvement plans
- Government-side procurement announcements not in SAM.gov

**When NOT to use:**
- Federal contract opportunities (use [`get_procurement_opportunities`](#get_procurement_opportunities))
- Historical awards (use [`get_contract_awards`](#get_contract_awards))
- Non-procurement general web search

```jsonc
search_web_procurement({
  "query": "water treatment plant RFP",
  "geography": "Texas",
  "sector": "water",              // transportation | water | energy | buildings | environmental
  "result_type": "rfp",           // rfp | bond | budget | award | any
  "limit": 8
})
```

**`AZURE_OPENAI_DEPLOYMENT_NAME` must be a model that supports `web_search_preview`** — gpt-4o or gpt-4.1 family. **NOT** gpt-4.1-nano.

---

## Chained patterns

Some user questions require multiple tools in sequence. The agent prompt names two canonical chains:

### BD research chain

Federal business-development queries should hit awards before opportunities:

```
User: "What federal highway construction opportunities are open under NAICS 237310 in Texas?"

→ get_contract_awards(query="highway construction", geography="TX", naics_codes=["237310"])
   (Who has won similar work recently? Typical award size? Incumbent contractors?)

→ get_procurement_opportunities(query="highway construction", geography="TX", naics_codes=["237310"])
   (What's open right now? Deadlines?)

→ Agent synthesizes: "Five contracts awarded in past 2 years averaging $4.2M. Three open
   opportunities matching — RFPs A, B, C with deadlines in 30 / 45 / 60 days. Incumbents:
   X, Y, Z. Recommend pursuing C — green-field, no incumbent advantage."
```

### Document drafting chain

Document drafts should pull firm precedent before invoking the template renderer:

```
User: "Draft an SOW for an IH-35 bridge rehabilitation"

→ search_project_knowledge(query="bridge rehabilitation SOW IH-35", document_types=["sow", "case_study"])
   (Pull firm templates, prior similar projects, vetted scope language)

→ draft_document(
    document_type="scope_of_work",
    context={ ...retrieved snippets... },
    project_name="IH-35 Bridge Rehabilitation"
  )
   (Template renderer produces the deliverable scaffold with retrieved context inlined)

→ Agent returns the Markdown SOW with placeholder fields the consultant fills in.
```

### Cross-domain risk audit

Some queries fan out across tools to build a composite picture:

```
User: "For Harris County, Texas: list structurally deficient bridges, recent flood declarations,
       and water systems with violations."

→ get_bridge_condition(state_code="48", county_code="201", structurally_deficient_only=true)
→ get_disaster_history(states=["TX"], incident_types=["Flood"], date_from="2020-01-01")
→ get_water_infrastructure(query_type="violations", states=["TX"], counties=["Harris"], has_violations=true)

→ Agent synthesizes a multi-hazard exposure summary tied to specific assets.
```

---

## Reference data

These are the standard values the agent fills into tool args. Keep them handy when debugging trace data.

### FIPS state codes (2-character, leading zero)

| State | Code | State | Code | State | Code | State | Code |
|---|---|---|---|---|---|---|---|
| AL | 01 | HI | 15 | MA | 25 | NM | 35 |
| AK | 02 | ID | 16 | MI | 26 | NY | 36 |
| AZ | 04 | IL | 17 | MN | 27 | NC | 37 |
| AR | 05 | IN | 18 | MS | 28 | ND | 38 |
| CA | 06 | IA | 19 | MO | 29 | OH | 39 |
| CO | 08 | KS | 20 | MT | 30 | OK | 40 |
| CT | 09 | KY | 21 | NE | 31 | OR | 41 |
| DE | 10 | LA | 22 | NV | 32 | PA | 42 |
| FL | 12 | ME | 23 | NH | 33 | TX | 48 |
| GA | 13 | MD | 24 | NJ | 34 | WA | 53 |

### AEC NAICS codes

| NAICS | Description |
|---|---|
| `237110` | Water & Sewer Line + Related Structures Construction |
| `237120` | Oil & Gas Pipeline Construction |
| `237130` | Power & Communication Line Construction |
| `237310` | Highway, Street & Bridge Construction |
| `237990` | Other Heavy & Civil Engineering Construction (dams, marine works) |
| `236220` | Commercial & Institutional Building Construction |
| `541310` | Architectural Services |
| `541330` | Engineering Services |
| `562910` | Remediation Services (environmental) |

### EIA fuel codes

| Code | Fuel |
|---|---|
| `SUN` | Solar |
| `WND` | Wind |
| `NG` | Natural Gas |
| `COL` | Coal |
| `NUC` | Nuclear |
| `HYC` | Conventional Hydro |
| `BIO` | Biomass |
| `GEO` | Geothermal |
| `PET` | Petroleum |

### Common FEMA incident types

`Flood`, `Hurricane`, `Severe Storm`, `Tornado`, `Fire`, `Earthquake`, `Drought`, `Severe Ice Storm`, `Winter Storm`, `Coastal Storm`, `Tropical Storm`, `Snow`.

### EPA water system types

| Code | Type | Population profile |
|---|---|---|
| `CWS` | Community Water System | Residential — serves the same population year-round |
| `NTNCWS` | Non-Transient Non-Community | Schools, factories, offices — same non-resident population ≥6 months/year |
| `TNCWS` | Transient Non-Community | Rest stops, campgrounds, parks — transient population |

---

<Aside type="note">
**Adding a new tool?** Update three things in lockstep:
1. The tool's `[Description(...)]` attribute on the C# `[McpServerTool]` method
2. The `ToolCatalog` constant in `services/agent-api-dotnet/Services/SuggestionService.cs`
3. This page

Drift between any of the three causes the agent to silently misroute queries.
</Aside>
