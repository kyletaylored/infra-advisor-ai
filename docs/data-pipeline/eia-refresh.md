---
title: EIA Energy Refresh
parent: Data Pipeline
nav_order: 3
---

# EIA Energy Refresh

**DAG ID:** `eia_refresh`  
**Schedule:** Weekly, 04:00 UTC  
**Data source:** [EIA API v2](https://www.eia.gov/opendata/) — U.S. Energy Information Administration  
**Coverage:** All US states

## Purpose

EIA provides authoritative data on US electricity generation, capacity, and fuel mix. This DAG indexes state-level energy statistics to support queries like:
- "What is Texas's renewable energy capacity breakdown by fuel type?"
- "Compare solar generation growth in California vs Texas from 2020–2024"
- "Show states with the highest natural gas generation share"

## Task structure

```
fetch_eia_data
  └── Paginate EIA API v2 endpoints
      Endpoints:
        - /v2/electricity/electric-power-operational-data (generation)
        - /v2/electricity/capacity (installed capacity by fuel)
      Auth: EIA_API_KEY header
  └── XCom push: list of generation/capacity records

store_raw_parquet
  └── Serialize to Parquet
  └── Upload: raw-data/eia/eia_generation_YYYYMMDD.parquet

index_to_search
  └── For each record:
        - Generate narrative text (state, fuel type, year, MWh/MW)
        - Embed via text-embedding-3-small
        - Upsert to Azure AI Search
```

## Data fields

| Field | Description |
|-------|-------------|
| `state` | State abbreviation |
| `fuelTypeCode` | COL, NG, NUC, SUN, WND, WAT, GEO, OTH |
| `fuelType` | Human-readable fuel type name |
| `year` | Calendar year |
| `generation_mwh` | Annual generation in megawatt-hours |
| `capacity_mw` | Installed capacity in megawatts |

**What EIA data does NOT include:** per-plant age, cost data, capital investment (capex), infrastructure vulnerability assessments, or resilience metrics. The tool returns generation and capacity aggregates only.

## Fuel type codes

| Code | Fuel type |
|------|-----------|
| `COL` | Coal |
| `NG` | Natural gas |
| `NUC` | Nuclear |
| `SUN` | Solar |
| `WND` | Wind |
| `WAT` | Conventional hydroelectric |
| `GEO` | Geothermal |
| `OTH` | Other (biomass, petroleum, etc.) |

## AI Search document structure

```json
{
  "id": "eia_TX_WND_2023",
  "content": "Texas wind generation in 2023: 133,000,000 MWh generation. Installed capacity: 37,500 MW. Wind accounts for 24% of Texas total generation.",
  "content_vector": [0.019, -0.038, ...],
  "source": "EIA",
  "domain": "energy",
  "document_type": "energy_statistics",
  "state": "TX"
}
```

## API key

EIA API v2 requires a free API key from [eia.gov/opendata/register.php](https://www.eia.gov/opendata/register.php). Set it as `EIA_API_KEY` in `.env` and in the `airflow-azure-secret` Kubernetes secret.
