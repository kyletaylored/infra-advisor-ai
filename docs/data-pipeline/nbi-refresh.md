---
title: NBI Bridge Refresh
parent: Data Pipeline
nav_order: 1
---

# NBI Bridge Refresh

**DAG ID:** `nbi_refresh`  
**Schedule:** Weekly, Sunday 03:00 UTC  
**Data source:** [FHWA National Bridge Inventory](https://www.fhwa.dot.gov/bridge/nbi.cfm) via ArcGIS REST FeatureServer  
**Coverage:** Texas (State Code 48)

## Purpose

The FHWA NBI is the most comprehensive bridge inspection database in the United States. This DAG pulls all Texas bridge records with non-null sufficiency ratings, converts condition codes to human-readable labels, and indexes each bridge as a searchable document in Azure AI Search.

The resulting index enables queries like:
- "Show bridges in Harris County with sufficiency rating below 50"
- "List scour-critical bridges on I-10 in Texas"
- "Which Texas bridges are structurally deficient with ADT over 5,000?"

## Task structure

```
fetch_nbi_bridges
  └── Paginates ArcGIS FeatureServer (2,000 records/page)
  └── Filter: STATE_CODE_001 = '48', SUFFICIENCY_RATING IS NOT NULL
  └── XCom push: list of bridge dicts

store_raw_parquet
  └── XCom pull bridge records
  └── Convert to Pandas DataFrame
  └── Serialize to Parquet
  └── Upload: raw-data/nbi/texas/nbi_tx_YYYYMMDD.parquet
  └── dd_upload_blob() → Datadog APM span

index_to_search
  └── XCom pull bridge records
  └── For each bridge:
        - Generate 500-character narrative text
        - Embed via text-embedding-3-small
        - Upsert to Azure AI Search
  └── Batch size: 100 documents
```

## Data fields

These are the exact FHWA field names used throughout the codebase. Do not rename them.

| Field name | Description |
|------------|-------------|
| `STRUCTURE_NUMBER_008` | Unique structure ID |
| `FACILITY_CARRIED_007` | Road/highway/rail carried by bridge |
| `LOCATION_009` | Location description |
| `STATE_CODE_001` | State FIPS code (48 = Texas) |
| `COUNTY_CODE_003` | County FIPS code |
| `YEAR_BUILT_027` | Year of construction |
| `ADT_029` | Annual Average Daily Traffic |
| `DECK_COND_058` | Deck condition code (0–9) |
| `SUPERSTRUCTURE_COND_059` | Superstructure condition code (0–9) |
| `SUBSTRUCTURE_COND_060` | Substructure condition code (0–9) |
| `CULVERT_COND_062` | Culvert condition code (0–9) |
| `SUFFICIENCY_RATING` | Sufficiency rating (0–100) |
| `STRUCT_DEFICIENT_IND` | Structurally deficient flag |
| `FRACTURE_CRITICAL_IND` | Fracture-critical flag |
| `SCOUR_CRITICAL_IND` | Scour-critical flag |
| `LAST_ADT_YEAR` | Year of last ADT measurement |
| `YEAR_RECONSTRUCTED_106` | Year of last major reconstruction |
| `LATITUDE_016` | Bridge latitude |
| `LONGITUDE_017` | Bridge longitude |

## Condition code labels

Condition codes (0–9) are decoded to human-readable labels in the tool response:

| Code | Label |
|------|-------|
| 9 | Excellent |
| 8 | Very Good |
| 7 | Good |
| 6 | Satisfactory |
| 5 | Fair |
| 4 | Poor |
| 3 | Serious |
| 2 | Critical |
| 1 | "Imminent" Failure |
| 0 | Failed |
| N | Not applicable |

## AI Search document structure

Each bridge produces one or more documents in Azure AI Search:

```json
{
  "id": "nbi_tx_4803000100",
  "content": "Bridge on I-45 in Harris County TX. Built 1972. ADT: 45,000. Deck: Fair (5), Superstructure: Poor (4), Substructure: Fair (5). Sufficiency: 47.8. Scour-critical: yes.",
  "content_vector": [0.012, -0.034, ...],
  "source": "FHWA_NBI",
  "domain": "transportation",
  "document_type": "asset_record",
  "state": "TX",
  "county": "Harris"
}
```

## Volume

The Texas NBI dataset contains approximately **615,000+ bridge records**. With 500-character chunk size, this produces approximately 615,000 documents in Azure AI Search. Incremental updates (weekly) upsert changed records; unchanged records are re-upserted idempotently.
