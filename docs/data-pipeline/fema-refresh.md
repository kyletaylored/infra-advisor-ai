---
title: FEMA Disaster Refresh
parent: Data Pipeline
nav_order: 2
---

# FEMA Disaster Refresh

**DAG ID:** `fema_refresh`  
**Schedule:** Daily, 02:00 UTC  
**Data source:** [OpenFEMA REST API v2](https://www.fema.gov/about/reports-and-data/openfema) — DisasterDeclarationsSummaries  
**Coverage:** All US states and territories, 2010 to present

## Purpose

Federal disaster declarations provide a historical record of major weather events, infrastructure emergencies, and community recovery needs. This DAG enables queries like:
- "Which Texas counties have had the most repeat hurricane declarations since 2010?"
- "Show me FEMA major disaster declarations for flood events in Louisiana"
- "What communities had both DR and EM declarations in the same year?"

Daily refresh ensures new declarations (which can be issued within days of a disaster) appear in the knowledge base promptly.

## Task structure

```
fetch_fema_declarations
  └── Paginate OpenFEMA REST API
      URL: https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries
      Filter: declarationDate >= 2010-01-01
      Pagination: $skip / $top (1,000 per page)
  └── XCom push: list of declaration dicts

store_raw_parquet
  └── XCom pull declarations
  └── Serialize to Parquet
  └── Upload: raw-data/fema/fema_declarations_YYYYMMDD.parquet

index_to_search
  └── XCom pull declarations
  └── For each declaration:
        - Generate narrative text
        - Token-chunk: 512-token windows, 64-token overlap (tiktoken)
        - Embed each chunk via text-embedding-3-small
        - Upsert chunks to Azure AI Search
```

## Data fields

| Field | Description |
|-------|-------------|
| `disasterNumber` | FEMA disaster number (e.g., DR-4611) |
| `declarationTitle` | Descriptive title (e.g., "HURRICANE IDA") |
| `state` | Two-letter state code |
| `designatedArea` | County or area name |
| `incidentType` | Flood, Hurricane, Tornado, Wildfire, Earthquake, Winter Storm, etc. |
| `declarationType` | DR (major disaster), EM (emergency), FM (fire management) |
| `declarationDate` | Date FEMA issued the declaration |
| `incidentBeginDate` | When the incident began |
| `incidentEndDate` | When the incident ended (null if ongoing) |
| `closeoutDate` | When the disaster program period closed |
| `ihProgramDeclared` | Individual Household Program declared |
| `iaProgramDeclared` | Individual Assistance declared |
| `paProgramDeclared` | Public Assistance declared |
| `hmProgramDeclared` | Hazard Mitigation declared |

## AI Search document structure

```json
{
  "id": "fema_DR4611_harris_tx_chunk_0",
  "content": "Major Disaster Declaration DR-4611 in Harris County, TX. Hurricane Ida. Declared 2021-08-29. Individual Assistance and Public Assistance declared. Incident period: 2021-08-26 to 2021-09-15.",
  "content_vector": [0.021, -0.043, ...],
  "source": "OpenFEMA",
  "domain": "environmental",
  "document_type": "disaster_declaration",
  "state": "TX",
  "county": "Harris"
}
```

## Volume

OpenFEMA maintains approximately 80,000+ disaster declaration records from 2010 onward. With 512-token chunking and 64-token overlap, the typical daily increment is small (a handful of new declarations). Full re-index runs in under 5 minutes.
