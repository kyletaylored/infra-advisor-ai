---
title: Azure Infrastructure
parent: Architecture
nav_order: 3
---

# Azure Infrastructure

All Azure resources are defined as Infrastructure as Code using Azure Bicep in `infra/bicep/`. A single subscription-scoped deployment creates everything in the `rg-tola-infra-advisor-ai` resource group in `eastus`.

## Azure resources

### AKS Cluster (`aks.bicep`)

| Property | Value |
|----------|-------|
| Node count | 3 |
| VM size | Standard_D2s_v3 (2 vCPU, 8 GB RAM each) |
| Total cluster RAM | 24 GB |
| Kubernetes version | 1.30+ |
| Node OS | Ubuntu 22.04 LTS |
| Networking | Azure CNI |
| Identity | System-assigned managed identity |

The 24 GB total RAM supports all workloads with the LocalExecutor Airflow setup. PySpark jobs (future roadmap) would require larger nodes.

### Azure OpenAI (`azure-openai.bicep`)

| Deployment | Model | Version | SKU | Capacity | Use |
|------------|-------|---------|-----|----------|-----|
| `gpt-4.1-mini` | gpt-4.1-mini | 2025-04-14 | GlobalStandard | 250K TPM | Agent reasoning, planning, suggestions |
| `gpt-4.1` | gpt-4.1 | latest | GlobalStandard | 10K TPM | Deep synthesis queries |
| `gpt-5.4-mini` | gpt-5.4-mini | 2026-03-17 | GlobalStandard | 250K TPM | Future model upgrade |
| `text-embedding-3-small` | text-embedding-3-small | 1 | Standard | 350K TPM | Vector embeddings for AI Search |

Deployments are chained sequentially (each `dependsOn` the previous) to avoid Azure provisioning conflicts.

### Azure AI Search (`azure-ai-search.bicep`)

| Property | Value |
|----------|-------|
| SKU | Standard |
| Partitions | 1 |
| Replicas | 1 |
| Index name | `infra-advisor-knowledge` |
| Search mode | Hybrid (vector + BM25 keyword) |
| Vector dimensions | 1536 (text-embedding-3-small) |

The single index stores all domain knowledge. Documents are tagged with `domain`, `source`, and `document_type` fields to enable filtered search by knowledge area.

**Index schema:**

| Field | Type | Purpose |
|-------|------|---------|
| `id` | String (key) | Unique document ID |
| `content` | String (searchable) | Text chunk (500–512 tokens) |
| `content_vector` | Collection(Single) | 1536-dim embedding |
| `source` | String (filterable) | Origin system (FHWA_NBI, OpenFEMA, EIA, etc.) |
| `domain` | String (filterable) | Knowledge area (transportation, water, energy, environmental, business_development) |
| `document_type` | String (filterable) | Record type (asset_record, disaster_declaration, water_plan_project, etc.) |
| `state` | String (filterable) | US state (where applicable) |
| `county` | String (filterable) | County name (where applicable) |
| `metadata` | String | JSON blob of source-specific fields |

### Azure Blob Storage (`azure-storage.bicep`)

| Property | Value |
|----------|-------|
| Redundancy | Standard LRS |
| Tier | Hot |
| Access | Private (SAS / connection string) |

**Container paths:**

| Path | Contents |
|------|----------|
| `raw-data/nbi/texas/` | NBI bridge parquet files (weekly) |
| `raw-data/fema/` | FEMA declaration parquet files (daily) |
| `raw-data/eia/` | EIA energy parquet files (weekly) |
| `raw-data/twdb/` | TWDB water plan Excel files (monthly) |
| `raw-data/epa_sdwis/` | EPA SDWIS water system parquet files (monthly) |
| `raw-data/knowledge-docs/` | Synthetic knowledge base parquet (on-demand) |
| `raw-data/awards/` | USASpending contract award parquet (weekly) |

### Redis (Kubernetes, not Azure PaaS)

Redis runs as a single-pod Kubernetes Deployment in the `infra-advisor` namespace. It is not Azure Cache for Redis — intentionally kept in-cluster to eliminate external latency on the hot session-read path.

| Property | Value |
|----------|-------|
| Image | `redis:7.4-alpine` |
| Persistence | None (in-memory only — session loss on restart is acceptable) |
| Port | 6379 (ClusterIP only) |

### PostgreSQL (Kubernetes StatefulSet)

Auth API user accounts are stored in a PostgreSQL 16 StatefulSet in the `infra-advisor` namespace. Airflow has its own separate PostgreSQL sidecar (managed by the Helm chart) in the `airflow` namespace.

| Property | Value |
|----------|-------|
| Image | `postgres:16-alpine` |
| Storage | Azure File Share CSI (azurefile-csi driver) |
| Port | 5432 (ClusterIP only) |

## Deploying infrastructure

```bash
# Prerequisites: az CLI logged in, subscription set
make deploy-infra
```

This runs:
```bash
az deployment sub create \
  --location eastus \
  --template-file infra/bicep/main.bicep \
  --parameters @infra/bicep/parameters/dev.bicepparam
```

**First-time deployment order:**

1. `make deploy-infra` — provision Azure resources
2. `make get-credentials` — fetch kubeconfig for AKS
3. `make create-secrets` — push all K8s secrets from `.env`
4. `make deploy-k8s` — apply all manifests and Helm releases

## Resource tags

All Azure resources are tagged:

| Tag | Value |
|-----|-------|
| `environment` | `dev` |
| `project` | `infra-advisor-ai` |
| `managed-by` | `bicep` |
