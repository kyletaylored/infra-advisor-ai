# InfraAdvisor AI

An AI-powered infrastructure advisory platform for consulting firms. Agents answer questions about bridge conditions, disaster history, energy infrastructure, and water systems using live government data sources — deployed on Azure Kubernetes Service with full Datadog observability.

Built as a reference architecture for AI agent systems with [Model Context Protocol (MCP)](https://modelcontextprotocol.io), [LangChain](https://python.langchain.com), [Azure OpenAI](https://azure.microsoft.com/en-us/products/ai-services/openai-service), and [Datadog](https://www.datadoghq.com).

---

## Architecture

```
React UI
  └─► Agent API (LangChain + Azure OpenAI gpt-4.1-mini)
        ├─► MCP Server (6 tools: bridge, disaster, energy, water, knowledge, docs)
        │     ├─► FHWA NBI, OpenFEMA, EIA, EPA (public APIs)
        │     └─► Azure AI Search (vector knowledge base)
        ├─► Redis (session memory)
        └─► Kafka (query events → eval pipeline)

Load Generator (CronJob) ──► Kafka ──► Agent API (eval scoring)
Airflow DAGs ──► Government data sources ──► Azure AI Search
Datadog Agent ──► APM, LLM Observability, RUM, DSM, CSPM
```

## Services

| Service | Language | Description |
|---|---|---|
| [`services/mcp-server`](services/mcp-server/) | Python 3.12 | FastMCP server with 6 infrastructure tools |
| [`services/agent-api`](services/agent-api/) | Python 3.12 | FastAPI + LangChain agent, Redis session memory |
| [`services/load-generator`](services/load-generator/) | Python 3.12 | Kafka producer, synthetic query corpus |
| [`services/ui`](services/ui/) | TypeScript / React 18 | Chat UI with bridge cards and citation panel |
| [`services/ingestion`](services/ingestion/) | Python 3.12 | Airflow DAGs for 5 government data sources |

## Prerequisites

- **Azure**: subscription with Contributor access
- **Azure CLI** (`az`), **kubectl**, **kubelogin**, **Helm 3**
- **Python 3.12** + [uv](https://docs.astral.sh/uv/)
- **Docker** (or Podman) for local builds
- **Datadog account** (US3 site) with API + App keys
- **EIA API key** (free at [eia.gov](https://www.eia.gov/opendata/))

## Quick Start

### 1. Configure environment

```bash
cp .env.example .env
# Fill in all values — see .env.example for required keys
```

### 2. Deploy Azure infrastructure

```bash
make deploy-infra        # deploys AKS, Azure OpenAI, AI Search via Bicep
make get-credentials     # fetches kubeconfig
kubelogin convert-kubeconfig -l azurecli
```

### 3. Create Kubernetes secrets

```bash
# Datadog
kubectl create secret generic datadog-secret -n datadog \
  --from-literal=api-key="$(grep ^DD_API_KEY= .env | cut -d= -f2-)" \
  --from-literal=app-key="$(grep ^DD_APP_KEY= .env | cut -d= -f2-)" \
  --from-literal=site="$(grep ^DD_SITE= .env | cut -d= -f2-)"

# App services
kubectl create secret generic mcp-server-secret -n infra-advisor \
  --from-literal=AZURE_OPENAI_ENDPOINT="$(grep ^AZURE_OPENAI_ENDPOINT= .env | cut -d= -f2-)" \
  --from-literal=AZURE_OPENAI_API_KEY="$(grep ^AZURE_OPENAI_API_KEY= .env | cut -d= -f2-)" \
  --from-literal=AZURE_SEARCH_ENDPOINT="$(grep ^AZURE_SEARCH_ENDPOINT= .env | cut -d= -f2-)" \
  --from-literal=AZURE_SEARCH_API_KEY="$(grep ^AZURE_SEARCH_API_KEY= .env | cut -d= -f2-)" \
  --from-literal=DD_API_KEY="$(grep ^DD_API_KEY= .env | cut -d= -f2-)"

# Repeat for agent-api-secret and airflow-azure-secret (see docs/agent-guides/build-test-verify.md)

make create-ghcr-secret  # GHCR imagePullSecret
```

### 4. Deploy to Kubernetes

```bash
make deploy-k8s          # applies all K8s manifests
make run-dags            # triggers all 5 Airflow ingestion DAGs
```

### 5. Access the UI

```bash
kubectl port-forward -n infra-advisor svc/ui 3000:80
# Open http://localhost:3000
```

## Local Development

Run tests for any service:

```bash
uv run pytest -x services/mcp-server/tests/
uv run pytest -x services/agent-api/tests/
uv run pytest -x services/load-generator/tests/
```

## CI/CD

| Workflow | Trigger | Description |
|---|---|---|
| [CI](.github/workflows/ci.yml) | push / PR | Runs pytest for all Python services |
| [Build & Push](.github/workflows/build-push.yml) | push to main | Builds Docker images and pushes to GHCR |

Images are published to `ghcr.io/kyletaylored/infra-advisor-ai/{service}:latest`.

## Documentation

- [Project Map](docs/agent-guides/project-map.md) — services, namespaces, dependencies, API endpoints
- [Build, Test & Verify](docs/agent-guides/build-test-verify.md) — commands for every phase
- [Core Conventions](docs/agent-guides/core-conventions.md) — coding standards and patterns
- [PRD v1.3](specs/infraadvisor-prd.md) — product requirements and design decisions

## Key Design Decisions

- **MCP for tool abstraction** — the agent never calls government APIs directly; all data access goes through versioned MCP tools
- **No LLM calls in MCP server** — `draft_document` uses Jinja2 templates only; LLM reasoning stays in the agent layer
- **Airflow for ingestion** — Datadog Data Jobs Monitoring (DJM) provides DAG-level observability
- **Kafka for eval pipeline** — load generator → `infra.query.events` → agent → `infra.eval.results`, monitored via DSM
- **LLM Observability** — faithfulness scoring runs async via `gpt-4.1-nano` after every agent response

## License

MIT
