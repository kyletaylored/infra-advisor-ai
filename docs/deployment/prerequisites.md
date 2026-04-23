---
title: Prerequisites
parent: Deployment
nav_order: 1
---

# Prerequisites

## Required tools

| Tool | Version | Install |
|------|---------|---------|
| `az` CLI | 2.60+ | `brew install azure-cli` |
| `kubelogin` | Latest | `brew install Azure/kubelogin/kubelogin` |
| `kubectl` | 1.30+ | `brew install kubectl` |
| `helm` | 3.14+ | `brew install helm` |
| `uv` | 0.5+ | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| `node` | 20+ | `brew install node@20` |
| `docker` | 24+ | Docker Desktop or Colima |

## Azure prerequisites

1. **Azure subscription** with Contributor role
2. **Service principal** with Contributor role on the subscription (for CI):
   ```bash
   az ad sp create-for-rbac --name "infra-advisor-ci" \
     --role Contributor \
     --scopes /subscriptions/<subscription-id> \
     --sdk-auth
   ```
   Store the output JSON as `AZURE_CREDENTIALS` in GitHub repository secrets.

3. **Azure OpenAI access** — request at [aka.ms/oai/access](https://aka.ms/oai/access) if not already approved

## Datadog prerequisites

1. **Datadog account** on `us3.datadoghq.com`
2. **API key** (`DD_API_KEY`) — from Datadog → Organization Settings → API Keys
3. **Application key** (`DD_APP_KEY`) — for dashboard/monitor provisioning (optional)
4. **RUM application** — create a Browser RUM app to get `VITE_DD_RUM_APP_ID` and `VITE_DD_RUM_CLIENT_TOKEN`
5. **LLM Observability** — enabled by default on all plans that include APM

## External API keys

| Key | Required for | Obtain from |
|-----|-------------|------------|
| `EIA_API_KEY` | EIA energy data DAG | [eia.gov/opendata/register.php](https://www.eia.gov/opendata/register.php) (free) |
| `SAMGOV_API_KEY` | Procurement opportunities tool | [SAM.gov API](https://open.gsa.gov/api/opportunities-api/) (free government API key) |
| `TAVILY_API_KEY` | Web procurement search tool | [tavily.com](https://tavily.com) (freemium) |

All three are optional — the tools return structured errors if the keys are missing, and the agent handles gracefully.

## GitHub Container Registry (GHCR)

Images are pushed to GHCR by GitHub Actions using `GITHUB_TOKEN` (automatic for Actions runs). For manual pulls in AKS, create a PAT:

1. Go to GitHub → Settings → Developer Settings → Personal Access Tokens (Classic)
2. Scopes: `read:packages`
3. Store as `GHCR_PAT` in `.env`

Run `make create-ghcr-secret` to create the `ghcr-pull-secret` Kubernetes secret before `make deploy-k8s`.

## Environment file

Copy `.env.example` to `.env` and fill in all values:

```bash
cp .env.example .env
```

Required variables for `make check-env` (the preflight before `make deploy-k8s`):

```bash
# Azure OpenAI
AZURE_OPENAI_ENDPOINT=https://<your-resource>.openai.azure.com/
AZURE_OPENAI_API_KEY=<key>

# Azure AI Search
AZURE_SEARCH_ENDPOINT=https://<your-resource>.search.windows.net/
AZURE_SEARCH_API_KEY=<key>

# Azure Storage
AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=https;...

# Database (Auth API)
POSTGRES_USER=authuser
POSTGRES_PASSWORD=<choose-a-password>
POSTGRES_DB=authdb
DATABASE_URL=postgresql://authuser:<password>@postgres.infra-advisor.svc.cluster.local:5432/authdb
DD_POSTGRES_PASSWORD=<choose-for-datadog-monitoring-user>

# Auth
JWT_SECRET=<output of: openssl rand -hex 32>

# Datadog
DD_API_KEY=<key>

# GitHub
GHCR_PAT=<pat>
```

Optional (tools degrade gracefully without them):
```bash
EIA_API_KEY=<key>
SAMGOV_API_KEY=<key>
TAVILY_API_KEY=<key>
```
