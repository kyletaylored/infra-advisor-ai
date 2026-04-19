# InfraAdvisor AI — Build, Test, and Verify Commands

## Prerequisites

- `uv` installed (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- `kubectl` configured (`az aks get-credentials --resource-group rg-tola-infra-advisor-ai --name aks-infra-advisor`)
- `helm` installed
- `az` CLI installed and logged in
- `.env` file populated (copy from `.env.example`)

---

## Top-level Makefile targets

```bash
make deploy-infra          # Deploy Azure Bicep IaC (AKS, AI Search, OpenAI, etc.)
make create-ghcr-secret    # Create ghcr-pull-secret K8s Secret in infra-advisor namespace
make deploy-k8s            # Apply all Kubernetes manifests (namespaces, DD, Kafka, Redis, Airflow, services)
make run-dags              # Manually trigger all 5 Airflow DAGs via CLI
```

---

## Phase 1 — Infrastructure and Data Pipeline

### Bicep IaC

```bash
# Validate individual Bicep module
az bicep build --file infra/bicep/modules/aks.bicep
az bicep build --file infra/bicep/modules/azure-ai-search.bicep
az bicep build --file infra/bicep/modules/azure-openai.bicep
az bicep build --file infra/bicep/modules/kafka.bicep
az bicep build --file infra/bicep/modules/redis.bicep
az bicep build --file infra/bicep/modules/monitoring.bicep

# Validate main template
az bicep build --file infra/bicep/main.bicep

# Deploy (what-if first)
az deployment group what-if \
  --resource-group rg-tola-infra-advisor-ai \
  --template-file infra/bicep/main.bicep \
  --parameters infra/bicep/parameters/dev.bicepparam

# Deploy for real
make deploy-infra
```

### Kubernetes

```bash
# Dry-run validate a manifest
kubectl apply --dry-run=client -f k8s/namespace.yaml
kubectl apply --dry-run=client -f k8s/datadog/daemonset.yaml

# Apply all manifests
make deploy-k8s

# Check cluster state
kubectl get nodes
kubectl get pods -A
kubectl get pods -n infra-advisor
kubectl get pods -n kafka
kubectl get pods -n airflow
kubectl get pods -n datadog
```

### GHCR pull secret

```bash
# Requires GHCR_PAT and GITHUB_EMAIL env vars set
make create-ghcr-secret

# Verify
kubectl get secret ghcr-pull-secret -n infra-advisor
```

### Airflow DAGs

```bash
# Port-forward Airflow UI
kubectl port-forward -n airflow svc/airflow-webserver 8080:8080

# Trigger DAGs manually via CLI
kubectl exec -n airflow deploy/airflow-scheduler -- airflow dags trigger knowledge_base_init
kubectl exec -n airflow deploy/airflow-scheduler -- airflow dags trigger nbi_refresh
kubectl exec -n airflow deploy/airflow-scheduler -- airflow dags trigger fema_refresh
kubectl exec -n airflow deploy/airflow-scheduler -- airflow dags trigger eia_refresh
kubectl exec -n airflow deploy/airflow-scheduler -- airflow dags trigger twdb_water_plan_refresh

# Check DAG run status
kubectl exec -n airflow deploy/airflow-scheduler -- airflow dags list-runs -d knowledge_base_init
```

### DAG tests (mock external APIs)

```bash
cd services/ingestion
uv run pytest -x tests/
```

---

## Phase 2 — MCP Server

### Install dependencies

```bash
cd services/mcp-server
uv sync
```

### Run locally

```bash
cd services/mcp-server
uv run uvicorn src.main:app --reload --port 8000
```

### Test

```bash
# Run all MCP server tests
uv run pytest -x services/mcp-server/tests/

# Run individual test files
uv run pytest -x services/mcp-server/tests/test_bridge_condition.py
uv run pytest -x services/mcp-server/tests/test_disaster_history.py
uv run pytest -x services/mcp-server/tests/test_water_infrastructure.py
uv run pytest -x services/mcp-server/tests/test_project_knowledge.py
uv run pytest -x services/mcp-server/tests/test_draft_document.py

# Health check (after deployment or local run)
curl http://localhost:8000/health
# Expected: {"status": "ok", "tools": ["get_bridge_condition", "get_disaster_history", ...]}
```

### Deploy to K8s

```bash
kubectl apply -f k8s/mcp-server/
kubectl rollout status deploy/mcp-server -n infra-advisor --timeout=5m

# Verify
kubectl get pods -n infra-advisor -l app=mcp-server
kubectl logs -n infra-advisor deploy/mcp-server --tail=50
```

---

## Phase 3 — Agent API

### Install dependencies

```bash
cd services/agent-api
uv sync
```

### Run locally

```bash
cd services/agent-api
uv run uvicorn src.main:app --reload --port 8001
```

### Test

```bash
uv run pytest -x services/agent-api/tests/

# Health check
curl http://localhost:8001/health
# Expected: {"status": "ok", "mcp_connected": true, "llm_connected": true}

# Test a query
curl -X POST http://localhost:8001/query \
  -H "Content-Type: application/json" \
  -d '{"query": "Pull structurally deficient bridges in Texas with ADT over 10000"}'
```

### Deploy to K8s

```bash
kubectl apply -f k8s/agent-api/
kubectl rollout status deploy/agent-api -n infra-advisor --timeout=5m

kubectl get pods -n infra-advisor -l app=agent-api
kubectl logs -n infra-advisor deploy/agent-api --tail=50
```

---

## Phase 4 — Load Generator and Dashboards

### Install dependencies

```bash
cd services/load-generator
uv sync
```

### Test

```bash
uv run pytest -x services/load-generator/tests/
```

### Deploy CronJob

```bash
kubectl apply -f k8s/load-generator/
kubectl get cronjobs -n infra-advisor

# Check that it fires (manual trigger)
kubectl create job --from=cronjob/load-generator load-generator-manual-001 -n infra-advisor
kubectl logs -n infra-advisor job/load-generator-manual-001
```

### Datadog dashboards / monitors

```bash
# Apply dashboards via DD API (requires DD_API_KEY and DD_APP_KEY)
# Use the Datadog Terraform provider or API client for this step
# JSON files are in datadog/dashboards/ and datadog/monitors/
```

---

## Phase 5 — React UI

### Install dependencies

```bash
cd services/ui
npm install
```

### Run locally

```bash
cd services/ui
npm run dev
# Open http://localhost:5173
```

### Build

```bash
cd services/ui
npm run build
# Output in services/ui/dist/
```

### Deploy to K8s

```bash
kubectl apply -f k8s/ui/
kubectl rollout status deploy/ui -n infra-advisor --timeout=5m
kubectl get pods -n infra-advisor -l app=ui
```

---

## Docker image builds

```bash
# Build individual image
docker build -t ghcr.io/kyletaylored/infra-advisor-ai/mcp-server:local services/mcp-server/
docker build -t ghcr.io/kyletaylored/infra-advisor-ai/agent-api:local services/agent-api/
docker build -t ghcr.io/kyletaylored/infra-advisor-ai/load-generator:local services/load-generator/
docker build -t ghcr.io/kyletaylored/infra-advisor-ai/ui:local services/ui/

# Push (CI handles this automatically on merge to main)
docker push ghcr.io/kyletaylored/infra-advisor-ai/mcp-server:local
```

---

## CI/CD

```bash
# Trigger CI locally (act — optional)
act push

# Check GitHub Actions in repo
gh run list
gh run view <run-id>
```

---

## Verify Datadog instrumentation

```bash
# Check APM traces are flowing
# (requires DD_API_KEY — query via API or check DD UI)

# Check custom metrics
# Search in DD Metrics Explorer: mcp.tool.calls, mcp.tool.latency_ms

# Check LLM Observability
# DD UI → LLM Observability → Applications → infra-advisor-ai

# Check DSM topology
# DD UI → Data Streams Monitoring

# Check DJM
# DD UI → Data Jobs Monitoring → Pipelines
```

---

## Common troubleshooting

```bash
# Pod not starting — check events
kubectl describe pod <pod-name> -n infra-advisor

# Image pull error — check secret
kubectl get secret ghcr-pull-secret -n infra-advisor
make create-ghcr-secret  # recreate if missing

# Airflow DAG import error
kubectl logs -n airflow deploy/airflow-scheduler | grep ERROR

# Redis connectivity
kubectl exec -n infra-advisor deploy/agent-api -- redis-cli -h redis ping

# Kafka connectivity
kubectl exec -n kafka deploy/kafka-cluster-entity-operator -- \
  kafka-topics.sh --bootstrap-server localhost:9092 --list

# MCP server not responding
kubectl port-forward -n infra-advisor svc/mcp-server 8000:8000
curl http://localhost:8000/health
```
