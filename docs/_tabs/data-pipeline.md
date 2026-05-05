---
title: Data Pipeline
icon: fas fa-database
order: 4
permalink: /data-pipeline/
---

Five Apache Airflow DAGs ingest real US government data on a recurring schedule, store raw records in Azure Blob Storage as Parquet files, and index searchable chunks into Azure AI Search. Together they maintain a continuously updated knowledge base of 600k+ infrastructure records.

## How the pipeline works

Every DAG follows the same three-task pattern:

```
Task 1: fetch_*_data()
  Paginate external government API
  XCom push: list of normalized record dicts

Task 2: store_raw_parquet()
  XCom pull records → Pandas DataFrame → Parquet bytes
  Upload to Azure Blob Storage via dd_upload_blob()
  (emits azure.blob.upload APM span with blob.size_bytes metric)

Task 3: index_to_search()
  XCom pull records
  For each record:
    1. Generate narrative text
    2. Chunk (character window or tiktoken 512-token / 64-overlap)
    3. Embed: Azure OpenAI text-embedding-3-small → 1536-dim vector
    4. Build Azure AI Search document {id, content, content_vector, source, domain, …}
  Upsert in 100-doc batches
```

## DAG schedule summary

| DAG | Schedule | Source | Volume |
|-----|----------|--------|--------|
| [fema_refresh](fema-refresh) | Daily 02:00 UTC | OpenFEMA REST | Declarations since 2010 |
| [nbi_refresh](nbi-refresh) | Weekly Sun 03:00 UTC | FHWA NBI ArcGIS | 615k+ TX bridges |
| [eia_refresh](eia-refresh) | Weekly 04:00 UTC | EIA API v2 | State generation/capacity |
| [twdb_water_plan_refresh](twdb-refresh) | Monthly 1st 05:00 UTC | TWDB Excel + EPA SDWIS | ~3k projects + ~3.5k water systems |
| [knowledge_base_init](knowledge-base-init) | On-demand | LLM synthetic generation | Firm knowledge documents |

## Azure AI Search index domains

All DAGs write to the single `infra-advisor-knowledge` index. Documents are tagged by `domain` for filtered search:

| Domain | Source DAGs | Example document types |
|--------|------------|----------------------|
| `transportation` | nbi_refresh | Bridge condition records |
| `environmental` | fema_refresh | Disaster declarations |
| `energy` | eia_refresh | State electricity statistics |
| `water` | twdb_water_plan_refresh | Water plan projects, water system records |
| `business_development` | samgov_awards_refresh, census_market_intelligence_refresh | Contract awards, market data |
| `synthetic` | knowledge_base_init | Firm proposals, lessons learned, cost guides |

## Airflow setup

The Airflow scheduler runs as a `StatefulSet` (`airflow-scheduler-0`) in the `airflow` namespace, using **LocalExecutor** — tasks run as subprocesses inside the scheduler pod, avoiding separate worker pods on the resource-constrained 24 GB cluster.

**Access the Airflow UI:**
```
https://infra-advisor-ai.kyletaylor.dev/airflow
Credentials: admin / admin
```

**Sync and run DAGs:**
```bash
make sync-dags    # kubectl cp DAG files to the PVC
make run-dags     # trigger all 5 DAGs
```

## Datadog Data Jobs Monitoring

All DAGs emit OpenLineage events to Datadog DJM via the Airflow OpenLineage provider:

```
AIRFLOW__LINEAGE__BACKEND=openlineage.lineage_backend.OpenLineageBackend
OPENLINEAGE__TRANSPORT__TYPE=datadog
```

Navigate to **Datadog → Data Observability → Data Jobs** to see run duration, task status, and lineage graphs for every DAG execution.

## Log-trace correlation

The scheduler uses a custom `DDJsonFormatter` (in `airflowLocalSettings`) that outputs structured JSON task logs with `dd.trace_id` and `dd.span_id` fields. `sitecustomize.py` in the DAGs folder ensures ddtrace is initialized in every LocalExecutor task subprocess, so task logs carry trace IDs even when tasks run in separate Python processes.

## Sections in this chapter

- [NBI Bridge Refresh](nbi-refresh) — 615k+ TX bridges weekly from FHWA, exact field names, condition codes
- [FEMA Disaster Refresh](fema-refresh) — Daily disaster declarations from OpenFEMA, token chunking
- [EIA Energy Refresh](eia-refresh) — Weekly state electricity generation/capacity from EIA API v2
- [TWDB Water Plan Refresh](twdb-refresh) — Monthly TWDB Excel + EPA SDWIS water systems
- [Knowledge Base Init](knowledge-base-init) — LLM-generated synthetic firm knowledge, on-demand trigger
