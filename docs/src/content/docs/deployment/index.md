---
title: Deployment
description: Deployment guide for InfraAdvisor AI on Azure Kubernetes Service
---

InfraAdvisor AI deploys to Azure Kubernetes Service in two phases: first provision Azure resources with Bicep, then apply Kubernetes manifests and Helm releases. A preflight `check-env` step validates all required environment variables before any cluster operations run.

## Deployment overview

```
Phase 1: Azure Infrastructure
  make deploy-infra
    └── az deployment sub create
          ├── AKS cluster (3× Standard_D2s_v3)
          ├── Azure OpenAI (4 model deployments)
          ├── Azure AI Search (Standard tier)
          └── Azure Blob Storage

Phase 2: Kubernetes Workloads
  make deploy-k8s
    ├── check-env (preflight — validates .env vars)
    ├── Namespaces
    ├── Strimzi CRDs + Kafka cluster + topics
    ├── Redis, PostgreSQL, Mailpit
    ├── Datadog Agent (DatadogAgent CR)
    ├── mcp-server, agent-api, auth-api, ui
    ├── load-generator CronJob
    └── Airflow (Helm install)

Phase 3: Data Initialization
  make sync-dags && make run-dags
    ├── kubectl cp DAG files to PVC
    └── Trigger all 9 Airflow DAGs
```

## Makefile reference

### Infrastructure

| Target | Description |
|--------|-------------|
| `make deploy-infra` | Run Bicep deployment (idempotent) |
| `make get-credentials` | Fetch AKS kubeconfig |
| `make deploy-k8s` | Apply all K8s manifests + Helm (runs `check-env` first) |
| `make check-env` | Validate all required `.env` variables |

### Secrets

| Target | Description |
|--------|-------------|
| `make create-secrets` | Create all K8s secrets at once |
| `make create-ghcr-secret` | GHCR image pull secret |
| `make create-mcp-server-secret` | Azure Search, OpenAI, EIA, SAM.gov |
| `make create-agent-api-secret` | Azure OpenAI endpoint + key (+ optional DATABASE_URL) |
| `make create-agent-api-dotnet-secret` | Azure OpenAI endpoint + key (+ optional DATABASE_URL) |
| `make create-auth-api-secret` | DATABASE_URL, JWT_SECRET |
| `make create-postgres-secret` | Postgres credentials |
| `make create-dd-postgres-secret` | Datadog DBM monitoring user password |
| `make create-airflow-secret` | Airflow Azure + Datadog secrets |
| `make create-load-generator-secret` | DD_API_KEY |

### Airflow

| Target | Description |
|--------|-------------|
| `make install-airflow` | Fresh Helm install (removes existing release first) |
| `make upgrade-airflow` | Helm upgrade with `k8s/airflow/values.yaml` |
| `make sync-dags` | `kubectl cp` DAG files to scheduler PVC |
| `make run-dags` | Trigger all 9 Airflow DAGs |

### Testing & verification

| Target | Description |
|--------|-------------|
| `make test-all` | Run pytest for all services |
| `make test-mcp` | MCP Server tests only |
| `make test-agent` | Agent API tests only |
| `make check-pods` | `kubectl get pods` across all namespaces |
| `make logs-mcp` | Tail MCP Server logs |
| `make logs-agent` | Tail Agent API logs |
| `make rollout-status` | Wait for all deployments to be ready |

## CI/CD (GitHub Actions)

Two workflows automate build and deployment on every merge to `main`:

**`ci.yml`** — Runs on every PR and push:
- pytest matrix for mcp-server and agent-api
- TypeScript type check (`tsc --noEmit`)

**`build-push.yml`** — Runs on merge to `main`:
- Detects which services changed (dorny/paths-filter)
- Builds and pushes Docker images to GHCR
- For changed services: `kubectl rollout restart deployment/<service>` on AKS
- For Airflow changes: `make upgrade-airflow` + `make sync-dags`

## Sections in this chapter

- [Prerequisites](/infra-advisor-ai/deployment/prerequisites/) — Required tools, Azure/Datadog setup, API keys, `.env` file reference
- [Quickstart](/infra-advisor-ai/deployment/quickstart/) — Step-by-step from zero to running application
- [Kubernetes Resources](/infra-advisor-ai/deployment/kubernetes/) — Full manifest inventory, resource sizes, common operations
- [Resource Group Notes](/infra-advisor-ai/resource-group-migration/) — Azure resource group constraints and migration history
