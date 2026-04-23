---
title: TWDB Water Plan Refresh
parent: Data Pipeline
nav_order: 4
---

# TWDB Water Plan Refresh

**DAG ID:** `twdb_water_plan_refresh`  
**Schedule:** Monthly, 1st of month 05:00 UTC  
**Data sources:**
1. [TWDB 2026 Regional Water Plans](https://www.twdb.texas.gov/waterplanning/data/rwp-database/index.asp) — Texas Water Development Board Excel workbook
2. [EPA SDWIS](https://enviro.epa.gov/enviro/efservice) — Safe Drinking Water Information System

**Coverage:** Texas community water systems and regional water plan projects

## Purpose

Water infrastructure planning is one of the most pressing infrastructure challenges in Texas. This DAG provides data for queries like:
- "What are the largest water supply projects planned for the Panhandle region?"
- "List Texas water utilities with health-based violations"
- "Show water plan projects with costs exceeding $500M in the 2050 planning horizon"

## Task structure

```
fetch_twdb_workbook
  └── HTTP GET: TWDB Excel workbook URL
  └── Upload raw Excel: raw-data/twdb/twdb_water_plan_YYYYMMDD.xlsx
  └── XCom push: list of water project dicts (all regions A–P)

fetch_epa_sdwis
  └── GET https://enviro.epa.gov/enviro/efservice/WATER_SYSTEM/STATE_CODE/TX/PWS_TYPE_CODE/CWS/JSON
  └── Paginate (1,000 per page)
  └── XCom push: list of water system dicts

store_raw_parquet (two tasks)
  └── Store TWDB projects: raw-data/twdb/twdb_projects_YYYYMMDD.parquet
  └── Store SDWIS systems: raw-data/epa_sdwis/sdwis_tx_cws_YYYYMMDD.parquet

index_to_search (two tasks)
  └── TWDB: token-chunk project narratives → embed → upsert
       document_type: water_plan_project, domain: water
  └── SDWIS: water system records → embed → upsert
       document_type: water_system_record, domain: water
```

## TWDB data fields

The Excel workbook uses varying column headers across regional plan versions. The DAG uses fuzzy column matching to normalize across sheets.

| Canonical field | Description |
|----------------|-------------|
| `project_name` | Name of water supply project |
| `sponsor` | Water user group / utility name |
| `county` | County where project is located |
| `region` | Regional water planning area (A–P) |
| `supply_type` | Groundwater, surface water, conservation, reuse, etc. |
| `strategy_type` | New supply, demand management, infrastructure improvement |
| `volume_2030`–`volume_2080` | Water supply volume (acre-feet/year) by decade |
| `cost_2030`–`cost_2080` | Project cost ($) by decade |

Cost data is available by decade: 2030, 2040, 2050, 2060, 2070, 2080.

## EPA SDWIS data fields

| Field | Description |
|-------|-------------|
| `pwsid` | Public water system ID |
| `pws_name` | System name |
| `pws_type_code` | CWS (community), NTNCWS, TNCWS |
| `primacy_agency_code` | State/EPA region with primacy |
| `population_served_count` | Estimated population served |
| `pws_activity_code` | Active, inactive, etc. |
| `source_water_type` | GW (groundwater), SW (surface water), GU |
| `violation_flag` | Has active or recent violations |

## AI Search document structure

**TWDB water project:**
```json
{
  "id": "twdb_project_harris_tx_1234",
  "content": "Water supply project: Lake Houston Aquifer Storage & Recovery. Sponsor: City of Houston. Harris County, Region H. Surface water reuse strategy. Volume 2030: 50,000 ac-ft/yr. Cost 2030: $340,000,000.",
  "content_vector": [0.015, -0.029, ...],
  "source": "TWDB_2026",
  "domain": "water",
  "document_type": "water_plan_project",
  "state": "TX",
  "county": "Harris"
}
```

**EPA water system:**
```json
{
  "id": "sdwis_TX0010001",
  "content": "Community water system: City of Houston PWS (TX0010001). Population served: 2,100,000. Source: surface water. Status: active. No current health-based violations.",
  "content_vector": [0.011, -0.022, ...],
  "source": "EPA_SDWIS",
  "domain": "water",
  "document_type": "water_system_record",
  "state": "TX",
  "county": "Harris"
}
```

## Volume

- TWDB: ~3,000 water supply projects across 16 regional planning areas (A–P)
- EPA SDWIS: ~3,500 Texas community water systems

Monthly refresh updates both datasets in full, as TWDB publishes workbook revisions and SDWIS violation status changes continuously.
