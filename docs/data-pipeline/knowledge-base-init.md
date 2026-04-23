---
title: Knowledge Base Init
parent: Data Pipeline
nav_order: 5
---

# Knowledge Base Init

**DAG ID:** `knowledge_base_init`  
**Schedule:** On-demand (manual trigger only)  
**Data source:** LLM-generated synthetic documents (Azure OpenAI gpt-4.1-mini)  
**Purpose:** Bootstrap the Azure AI Search index with firm-specific project knowledge

## Purpose

The other four DAGs populate the index with public government data. This DAG generates synthetic internal firm documents — project proposals, lessons learned, cost benchmarks, and risk frameworks — to simulate the type of institutional knowledge a consulting firm would have accumulated over years of project delivery.

This enables queries like:
- "What similar bridge rehabilitation projects has the firm delivered?"
- "What are our standard risk mitigation strategies for flood-prone bridge sites?"
- "Summarize lessons learned from previous FEMA grant applications"

Run this DAG once on initial deployment to seed the knowledge base before going live.

## Task structure

```
generate_firm_documents
  └── For each document type (proposal, lessons_learned, cost_guide, risk_framework):
        - Call gpt-4.1-mini with generation prompt
        - Produce 5–10 synthetic documents per type
  └── XCom push: list of document dicts

store_raw_parquet
  └── Serialize documents to Parquet
  └── Upload: raw-data/knowledge-docs/kb_YYYYMMDD.parquet

index_to_search
  └── For each document:
        - Chunk text (512-token windows, 64-token overlap)
        - Embed via text-embedding-3-small
        - Upsert to Azure AI Search
       document_type: varies, domain: synthetic (mixed)
```

## Document types generated

| Type | Description | Example content |
|------|-------------|-----------------|
| `project_proposal` | Fictional past project proposal | Scope, schedule, cost estimate, technical approach for a bridge rehab project |
| `lessons_learned` | Post-project reflection document | What went well, challenges, recommendations for similar projects |
| `cost_benchmark` | Historical cost data by project type | Unit costs for bridge deck replacement, water main replacement, etc. |
| `risk_framework` | Standard risk register templates | Risk categories, likelihood/impact matrices, standard mitigations for infrastructure projects |
| `funding_guide` | Federal funding program summaries | RAISE, INFRA, Bridge Formula Program, CDBG-DR eligibility and match requirements |

## Triggering the init DAG

```bash
# Via Makefile
make run-dags

# Via kubectl
kubectl exec -n airflow airflow-scheduler-0 -c scheduler -- \
  airflow dags trigger knowledge_base_init

# Via Airflow REST API (with auth token)
curl -X POST https://infra-advisor-ai.kyletaylor.dev/airflow/api/v2/dags/knowledge_base_init/dagRuns \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"logical_date": "2026-04-23T00:00:00Z"}'
```

## Azure AI Search dependency

The `search_project_knowledge` MCP tool depends on this index being populated. If the index doesn't exist, the tool returns a structured error:

```json
{
  "error": "Azure AI Search index 'infra-advisor-knowledge' not found",
  "action": "Run knowledge_base_init Airflow DAG (make run-dags) to initialize the index",
  "retriable": false
}
```

**Important:** Run `knowledge_base_init` before going live, or before demonstrating the knowledge search capability.
