---
title: Quickstart
parent: Deployment
nav_order: 2
---

# Quickstart

Complete deployment from a clean Azure subscription to a running application.

## 1. Clone and configure

```bash
git clone https://github.com/kyletaylored/infra-advisor-ai.git
cd infra-advisor-ai
cp .env.example .env
# Edit .env with your values (see Prerequisites)
set -a && source .env && set +a
```

## 2. Deploy Azure infrastructure

```bash
az login
az account set --subscription <your-subscription-id>
make deploy-infra
```

This provisions:
- AKS cluster (3× Standard_D2s_v3)
- Azure OpenAI (4 model deployments)
- Azure AI Search (Standard tier)
- Azure Blob Storage
- Log Analytics workspace

**Duration:** 10–15 minutes

## 3. Get AKS credentials

```bash
make get-credentials
kubectl get nodes   # verify 3 nodes Ready
```

## 4. Create GHCR pull secret

```bash
make create-ghcr-secret
```

## 5. Create all secrets

```bash
make create-secrets
```

This runs all individual secret targets:
- `create-mcp-server-secret`
- `create-agent-api-secret`
- `create-auth-api-secret`
- `create-postgres-secret`
- `create-dd-postgres-secret`
- `create-airflow-secret`
- `create-load-generator-secret`

## 6. Deploy Kubernetes workloads

```bash
make deploy-k8s
```

This applies in order:
1. Namespaces
2. Strimzi Operator CRDs (with `kubectl wait --for=condition=established`)
3. Kafka cluster and topics
4. Redis
5. PostgreSQL
6. Datadog Agent (DatadogAgent CR)
7. MailHog
8. MCP Server
9. Agent API
10. Auth API
11. Load Generator
12. UI
13. Airflow (Helm install)

**Duration:** 5–10 minutes for all pods to reach Running state.

## 7. Verify pods

```bash
kubectl get pods -n infra-advisor
kubectl get pods -n airflow
kubectl get pods -n kafka
kubectl get pods -n datadog
```

All pods should show `Running` status. The Airflow scheduler may take 3–5 minutes to complete `pip install` and start.

## 8. Initialize the knowledge base

```bash
make sync-dags      # copy DAG files to Airflow PVC
make run-dags       # trigger all 5 DAGs
```

The `knowledge_base_init` DAG must complete before `search_project_knowledge` returns results. Monitor progress in the Airflow UI:
```
https://infra-advisor-ai.kyletaylor.dev/airflow
```

## 9. Get the application URL

```bash
kubectl get svc -n infra-advisor ui -o jsonpath='{.status.loadBalancer.ingress[0].ip}'
```

Point your DNS record (or use the IP directly):
```
https://infra-advisor-ai.kyletaylor.dev
```

## 10. Register a user

Navigate to the application URL, click **Register**, and create your account. The first user becomes an admin automatically.

---

## Upgrade deployments

After pushing code changes (handled automatically by CI on merge to `main`):

```bash
# Manually force a rollout if needed:
kubectl rollout restart deployment/agent-api -n infra-advisor
kubectl rollout restart deployment/mcp-server -n infra-advisor
kubectl rollout restart deployment/auth-api -n infra-advisor
kubectl rollout restart deployment/ui -n infra-advisor
```

## Upgrade Airflow config

After changing `k8s/airflow/values.yaml`:

```bash
make upgrade-airflow
```

Note: The Makefile `upgrade-airflow` target will exit with error code 1 if the migration job has already been cleaned up by TTL — this is expected and safe. Check `helm list -n airflow` to confirm the upgrade succeeded (STATUS: deployed).

## Sync DAG changes

After modifying DAG files in `services/ingestion/dags/`:

```bash
make sync-dags
```

DAG changes are picked up by the dag-processor within 30 seconds.
