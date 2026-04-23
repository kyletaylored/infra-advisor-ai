---
title: Kubernetes Resources
parent: Deployment
nav_order: 3
---

# Kubernetes Resources

## Namespace layout

```
infra-advisor    Application services
airflow          Airflow data pipeline
kafka            Strimzi Kafka cluster
datadog          Datadog Agent DaemonSet
```

## infra-advisor namespace

### Deployments

| Name | Replicas | Image | Port | Resources (req/lim) |
|------|----------|-------|------|---------------------|
| `mcp-server` | 2 | `ghcr.io/.../mcp-server:latest` | 8000 | 256Mi/512Mi RAM, 250m/500m CPU |
| `agent-api` | 2 | `ghcr.io/.../agent-api:latest` | 8001 | 512Mi/1Gi RAM, 500m/1000m CPU |
| `auth-api` | 2 | `ghcr.io/.../auth-api:latest` | 8002 | 128Mi/256Mi RAM, 100m/500m CPU |
| `ui` | 2 | `ghcr.io/.../ui:latest` | 80 | 64Mi/128Mi RAM, 50m/100m CPU |
| `redis` | 1 | `redis:7.4-alpine` | 6379 | 128Mi/256Mi RAM, 100m/200m CPU |
| `mailhog` | 1 | `mailhog/mailhog:v1.0.1` | 1025/8025 | 64Mi/128Mi RAM |

### StatefulSet

| Name | Replicas | Storage | Purpose |
|------|----------|---------|---------|
| `postgres` | 1 | Azure File Share (azurefile-csi) | Auth API user database |

### CronJob

| Name | Schedule | Image | Purpose |
|------|----------|-------|---------|
| `load-generator` | `*/5 * * * *` | `ghcr.io/.../load-generator:latest` | Synthetic query load |

### Services

| Name | Type | Port(s) |
|------|------|---------|
| `mcp-server` | ClusterIP | 8000 |
| `agent-api` | ClusterIP | 8001 |
| `auth-api` | ClusterIP | 8002 |
| `ui` | LoadBalancer | 80 → 80 |
| `redis` | ClusterIP | 6379 |
| `postgres` | ClusterIP | 5432 |
| `mailhog` | ClusterIP | 1025 (SMTP), 8025 (HTTP) |

### Secrets

| Name | Contains |
|------|---------|
| `ghcr-pull-secret` | GHCR authentication for image pulls |
| `mcp-server-secret` | Azure Search, OpenAI, EIA, ERCOT, SAM.gov, Tavily keys |
| `agent-api-secret` | Azure OpenAI endpoint + API key |
| `auth-api-secret` | DATABASE_URL, JWT_SECRET |
| `postgres-secret` | POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB |
| `dd-postgres-secret` | DD_POSTGRES_PASSWORD (Datadog DBM monitoring user) |
| `load-generator-secret` | DD_API_KEY |

All deployments and the CronJob reference `ghcr-pull-secret` as `imagePullSecrets`.

## airflow namespace

Managed by Helm chart `apache-airflow/airflow` (release name: `airflow`).

| Component | Kind | Notes |
|-----------|------|-------|
| `airflow-scheduler` | StatefulSet (1 pod) | LocalExecutor; pip installs on startup |
| `airflow-api-server` | Deployment (1 pod) | Airflow 3.x web UI + REST API |
| `airflow-dag-processor` | Deployment (1 pod) | Parses and validates DAG files |
| `airflow-triggerer` | StatefulSet (1 pod) | Runs deferred/async operators |
| `airflow-postgresql` | StatefulSet (1 pod) | Airflow metadata DB |

**PVCs:**
- `logs-airflow-scheduler-0` — 2Gi, azurefile-csi (task logs)
- `dags-airflow-scheduler-0` — 1Gi, azurefile-csi, ReadWriteMany (DAG files)

The DAGs PVC is ReadWriteMany so `make sync-dags` can write while the scheduler is reading.

**Secret:** `airflow-azure-secret` — Azure OpenAI, Search, Storage keys + DD_API_KEY

## kafka namespace

| Component | Notes |
|-----------|-------|
| Strimzi Operator | Installed via `kubectl apply -f https://strimzi.io/install/latest?namespace=kafka` |
| `kafka-cluster` | KafkaCluster custom resource (3 brokers) |
| `infra.query.events` | KafkaTopic — load generator → agent API consumer |
| `infra.eval.results` | KafkaTopic — agent API producer (eval results) |

Bootstrap service: `kafka-cluster-kafka-bootstrap.kafka.svc.cluster.local:9092`

## datadog namespace

Managed by the Datadog Operator (installed via Helm).

| Component | Kind |
|-----------|------|
| `datadog-agent` | DaemonSet (1 pod per node = 3 pods) |
| `datadog-cluster-agent` | Deployment (1 pod) |

**Features enabled on the DatadogAgent CR:**
- APM (auto-instrumentation for Python 4 and JavaScript 5)
- Log collection (container logs + structured JSON)
- ASM (Application Security Management)
- CWS (Cloud Workload Security)
- CSPM (Cloud Security Posture Management)
- SBOM (container + host)
- USM (Universal Service Monitoring)
- NPM (Network Performance Monitoring)
- Live process collection
- Data Streams Monitoring

## Common operations

```bash
# Check all pods
make check-pods

# Tail agent-api logs
make logs-agent

# Tail mcp-server logs
make logs-mcp

# Restart a deployment (forces image pull)
kubectl rollout restart deployment/agent-api -n infra-advisor

# Scale a deployment
kubectl scale deployment/mcp-server --replicas=3 -n infra-advisor

# Exec into a pod
kubectl exec -it deploy/agent-api -n infra-advisor -- bash

# View configmap
kubectl get configmap agent-api-config -n infra-advisor -o yaml
```
