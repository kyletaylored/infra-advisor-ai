---
title: Project Map
parent: Development
nav_order: 4
---

# InfraAdvisor AI — Project Map

## Services

| Service | Directory | Language | Port | K8s Namespace | Image |
|---|---|---|---|---|---|
| InfraTools MCP Server | `services/mcp-server/` | Python 3.12 | 8000 | `infra-advisor` | `ghcr.io/kyletaylored/infra-advisor-ai/mcp-server` |
| Agent API | `services/agent-api/` | Python 3.12 | 8001 | `infra-advisor` | `ghcr.io/kyletaylored/infra-advisor-ai/agent-api` |
| Load Generator | `services/load-generator/` | Python 3.12 | — (batch) | `infra-advisor` | `ghcr.io/kyletaylored/infra-advisor-ai/load-generator` |
| React UI | `services/ui/` | TypeScript / React 18 | 3000 | `infra-advisor` | `ghcr.io/kyletaylored/infra-advisor-ai/ui` |
| Airflow (Helm) | `k8s/airflow/` | — | 8080 (UI) | `airflow` | apache/airflow (Helm chart) |
| Kafka (Strimzi) | `k8s/kafka/` | — | 9092 | `kafka` | strimzi/kafka |
| Redis | `k8s/redis/` | — | 6379 | `infra-advisor` | redis:7 |
| Datadog Agent | `k8s/datadog/` | — | 8126 (APM), 8125 (DogStatsD) | `datadog` | datadog/agent |

## Kubernetes Namespaces

| Namespace | Contents |
|---|---|
| `infra-advisor` | mcp-server, agent-api, load-generator, ui, redis |
| `kafka` | Strimzi operator, KafkaCluster CR, Kafka broker |
| `airflow` | Airflow scheduler, webserver, Postgres sidecar |
| `datadog` | DD Agent DaemonSet, ClusterAgent |

## Inter-Service Dependencies

```
React UI (port 3000)
  └─► Agent API (port 8001)  [HTTP POST /query]
        ├─► MCP Server (port 8000)  [MCP streamable HTTP]
        │     ├─► FHWA NBI ArcGIS REST API  [external]
        │     ├─► OpenFEMA REST API  [external]
        │     ├─► EIA API v2  [external]
        │     ├─► EPA Envirofacts SDWIS  [external]
        │     └─► Azure AI Search  [managed Azure service]
        ├─► Azure OpenAI GPT-4o  [managed Azure service]
        ├─► Redis (port 6379)  [session memory]
        └─► Kafka (port 9092)  [infra.query.events consumer]

Load Generator (CronJob)
  └─► Kafka (port 9092)  [infra.query.events producer]

Agent API
  └─► Kafka (port 9092)  [infra.eval.results producer]

Airflow DAGs
  ├─► FHWA NBI ArcGIS REST API  [external, weekly]
  ├─► OpenFEMA REST API  [external, daily]
  ├─► EIA API v2  [external, weekly]
  ├─► TWDB water plan workbook  [external, monthly]
  ├─► EPA Envirofacts SDWIS  [external, monthly]
  ├─► Azure OpenAI  [synthetic doc generation]
  ├─► Azure AI Search  [index upserts]
  └─► Azure Blob Storage  [raw parquet writes]

Datadog Agent DaemonSet
  └─► All pods (APM, logs, DogStatsD, JMX)
```

## Azure Resources (resource group: `rg-tola-infra-advisor-ai`)

| Resource | Type | Notes |
|---|---|---|
| `aks-infra-advisor` | AKS | 3 nodes, Standard_D2s_v3, K8s 1.30+ |
| Azure OpenAI | Cognitive Services | Deployments: `gpt-4.1-mini` (agent), `gpt-4.1-nano` (eval), `text-embedding-3-small` |
| Azure AI Search | Search service | Index: `infra-advisor-knowledge` |
| Azure Blob Storage | Storage account | Container: `infra-advisor-raw` (raw parquet) |
| Azure API Management | APIM | Routes external traffic to agent-api |

## Kafka Topics

| Topic | Producer | Consumer | Purpose |
|---|---|---|---|
| `infra.query.events` | Load Generator | Agent API | Synthetic query delivery |
| `infra.eval.results` | Agent API | (DD DSM monitoring) | Evaluation scores + latency |

## Azure AI Search Index

**Index name:** `infra-advisor-knowledge`

**Document sources loaded by Airflow DAGs:**
- `domain: "transportation"`, `document_type: "asset_record"` — FHWA NBI bridge records (Texas)
- `domain: "environmental"`, `document_type: "disaster_declaration"` — OpenFEMA disaster declarations
- `domain: "energy"`, `document_type: "energy_record"` — EIA state electricity data
- `domain: "water"`, `document_type: "water_plan_project"` — TWDB 2026 State Water Plan projects
- `domain: "water"`, `document_type: "water_system_record"` — EPA SDWIS Texas water systems
- `source: "synthetic"` — 80 synthetic firm knowledge base documents

## External API Endpoints

| Source | Base URL | Auth |
|---|---|---|
| FHWA NBI (BTS NTAD) | `https://services.arcgis.com/xOi1kZaI0eWDREZv/arcgis/rest/services/National_Bridge_Inventory/FeatureServer/0/query` | None |
| OpenFEMA | `https://www.fema.gov/api/open/v2/` | None |
| EIA API v2 | `https://api.eia.gov/v2/electricity/electric-power-operational-data/data/` | `EIA_API_KEY` env var |
| EPA Envirofacts SDWIS | `https://enviro.epa.gov/enviro/efservice/` | None |
| TWDB Water Plan | `https://www.twdb.texas.gov/waterplanning/data/rwp-database/index.asp` | None (batch download) |

## Internal K8s DNS Names

| Service | DNS Name |
|---|---|
| MCP Server | `mcp-server.infra-advisor.svc.cluster.local:8000` |
| Agent API | `agent-api.infra-advisor.svc.cluster.local:8001` |
| Redis | `redis.infra-advisor.svc.cluster.local:6379` |
| Kafka | `kafka-cluster-kafka-bootstrap.kafka.svc.cluster.local:9092` |
| Datadog Agent | `datadog-agent.datadog.svc.cluster.local:8126` (APM), `:8125` (DogStatsD) |
| Airflow Webserver | `airflow-webserver.airflow.svc.cluster.local:8080` |
