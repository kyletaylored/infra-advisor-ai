---
title: Resource Group Notes
parent: Deployment
nav_order: 4
---

# Resource Group Migration Plan

## Why there will always be two resource groups

Azure AKS **requires** a second resource group — this is a platform constraint, not a configuration
choice. When AKS is created it provisions a "node resource group" (historically prefixed `MC_`)
where it places the underlying compute infrastructure it owns and manages exclusively: VM scale sets,
NICs, OS disks, load balancers, and public IPs. You cannot merge these into your main resource group.

**What this means in practice:** every resource *you* own and configure is already in
`rg-tola-infra-advisor-ai`. The node RG is Azure's internal workspace. You never need to touch it.

The only thing that can be improved is renaming the node RG from the ugly auto-generated
`MC_rg-tola-infra-advisor-ai_aks-infra-advisor-dev_eastus` to something shorter. This requires
recreating the AKS cluster (see Part B below).

---

## Current state (as of 2026-04-19)

### `rg-tola-infra-advisor-ai` — your resource group

| Resource name | Type | Managed by | In use? | Action |
|---|---|---|---|---|
| `aks-infra-advisor-dev` | AKS cluster | Bicep (`aks.bicep`) | ✅ Yes | Keep |
| `oai-infra-advisor-dev` | Azure OpenAI | Bicep (`azure-openai.bicep`) | ❌ No — orphan in use instead | Switch live app to this |
| `srch-infra-advisor-dev` | Azure AI Search (Standard) | Bicep (`azure-ai-search.bicep`) | ❌ No — orphan in use instead | Switch live app to this |
| `law-infra-advisor-dev` | Log Analytics workspace | Bicep (`monitoring.bicep`) | ✅ Yes | Keep |
| `infra-advisor-openai` | Azure OpenAI | Manual (pre-Bicep) | ✅ Yes (live app) | Delete after switching |
| `infra-advisor-search` | Azure AI Search (Basic) | Manual (pre-Bicep) | ✅ Yes (live app) | Delete after switching |
| `vnet01` | Virtual Network | Manual (pre-Bicep) | Partially (OpenAI ACL) | Delete after switching |

### `MC_rg-tola-infra-advisor-ai_aks-infra-advisor-dev_eastus` — AKS-managed node RG

| Resource | Purpose | Yours to manage? |
|---|---|---|
| VM Scale Set (3× Standard_D2s_v3) | K8s worker nodes | No — AKS manages |
| NICs, OS disks | Node networking/storage | No — AKS manages |
| `52.226.61.98` (static public IP) | AKS outbound NAT | No — AKS manages |
| `20.75.178.33` (static public IP) | UI LoadBalancer (Cloudflare → app) | Indirectly — via `kubectl` |

### Key problem: live app uses the wrong resources

The K8s secret `agent-api-secret` currently points to the manual/orphan resources:
- `AZURE_OPENAI_ENDPOINT` = `https://infra-advisor-openai.openai.azure.com/` ← orphan
- `AZURE_SEARCH_ENDPOINT` = `https://infra-advisor-search.search.windows.net` ← orphan

The Bicep-managed resources (`oai-infra-advisor-dev`, `srch-infra-advisor-dev`) exist but are unused.

---

## Migration plan

### Part A — Switch live app to Bicep-managed resources
**Downtime: none.** Rolling secret update + pod restart.

#### Step 1: Configure `oai-infra-advisor-dev` network access
The orphan OpenAI has a network ACL allowing only the AKS outbound IP (`52.226.61.98`).
The Bicep OpenAI currently has no ACL (open). For dev this is fine as-is; optionally restrict it:

```bash
az cognitiveservices account update \
  --resource-group rg-tola-infra-advisor-ai \
  --name oai-infra-advisor-dev \
  --ip-rules "52.226.61.98"
```

#### Step 2: Update the K8s secret with Bicep-managed endpoints and keys

New values (pull actual keys from `.env` or Azure portal):
- `AZURE_OPENAI_ENDPOINT` = `https://oai-infra-advisor-dev.openai.azure.com/`
- `AZURE_OPENAI_API_KEY`  = *(key for `oai-infra-advisor-dev` — rotate after migration)*
- `AZURE_SEARCH_ENDPOINT` = `https://srch-infra-advisor-dev.search.windows.net`
- `AZURE_SEARCH_API_KEY`  = *(key for `srch-infra-advisor-dev` — rotate after migration)*

```bash
# Pull keys from Azure at migration time (do not hardcode here):
OAI_KEY=$(az cognitiveservices account keys list \
  -g rg-tola-infra-advisor-ai -n oai-infra-advisor-dev --query key1 -o tsv)
SEARCH_KEY=$(az search admin-key show \
  -g rg-tola-infra-advisor-ai --service-name srch-infra-advisor-dev --query primaryKey -o tsv)

kubectl create secret generic agent-api-secret \
  --namespace infra-advisor \
  --from-literal=AZURE_OPENAI_ENDPOINT=https://oai-infra-advisor-dev.openai.azure.com/ \
  --from-literal=AZURE_OPENAI_API_KEY=$OAI_KEY \
  --from-literal=AZURE_SEARCH_ENDPOINT=https://srch-infra-advisor-dev.search.windows.net \
  --from-literal=AZURE_SEARCH_API_KEY=$SEARCH_KEY \
  --from-literal=DD_API_KEY=$DD_API_KEY \
  --dry-run=client -o yaml | kubectl apply -f -
```

#### Step 3: Restart agent-api to pick up new secret

```bash
kubectl rollout restart deployment/agent-api -n infra-advisor
kubectl rollout status deployment/agent-api -n infra-advisor
```

#### Step 4: Verify end-to-end

```bash
curl -s https://infra-advisor-ai.kyletaylor.dev/api/health | python3 -m json.tool
# Expect: {"status":"ok","mcp_connected":true,"llm_connected":true}

curl -s -X POST https://infra-advisor-ai.kyletaylor.dev/api/query \
  -H 'Content-Type: application/json' \
  -d '{"query": "Show me 3 bridges in Texas.", "session_id": "migration-test"}' | python3 -m json.tool
# Expect: answer with bridge data, tools_called: ["get_bridge_condition"]
```

#### Step 5: Update `.env` to match Bicep-managed endpoints

Update `.env` with the Bicep-managed endpoints. Keys are retrieved from Azure portal or CLI
(`az cognitiveservices account keys list` / `az search admin-key show`) and should be rotated
once the orphan resources are deleted.

#### Step 6: Delete orphan resources

```bash
# Delete orphan OpenAI (has gpt-4o deployment — will be deleted with it)
az cognitiveservices account delete \
  --resource-group rg-tola-infra-advisor-ai \
  --name infra-advisor-openai

# Delete orphan Search (Basic SKU, no indexed data worth keeping)
az search service delete \
  --resource-group rg-tola-infra-advisor-ai \
  --name infra-advisor-search

# Delete vnet01 (only existed to satisfy OpenAI VNet ACL — no longer needed)
az network vnet delete \
  --resource-group rg-tola-infra-advisor-ai \
  --name vnet01
```

---

### Part B — Rename the AKS node resource group
**Downtime: ~15–20 minutes.** Cluster must be recreated. App is unreachable during this window.

This is cosmetic. The current node RG name (`MC_rg-tola-infra-advisor-ai_aks-infra-advisor-dev_eastus`)
is ugly but fully functional. Skip this if you don't care about the name.

If you proceed, the new node RG will be: **`rg-tola-infra-advisor-ai-nodes`**
(already set in `infra/bicep/modules/aks.bicep` via `nodeResourceGroup`).

**Note:** The UI LoadBalancer IP (`20.75.178.33`) lives in the node RG and will be released when
the cluster is deleted. After recreation, AKS will provision a **new public IP** — you will need
to update the Cloudflare DNS A record for `infra-advisor-ai.kyletaylor.dev`.

#### Step 1: Record all secrets before deleting the cluster

All secrets are already in `.env`. Double-check it has current values before proceeding.

#### Step 2: Delete the existing AKS cluster

```bash
az aks delete \
  --resource-group rg-tola-infra-advisor-ai \
  --name aks-infra-advisor-dev \
  --yes --no-wait
```

Watch deletion progress (~5–8 min):
```bash
az aks show -g rg-tola-infra-advisor-ai -n aks-infra-advisor-dev --query provisioningState -o tsv
# Returns "Deleting" then errors out when gone
```

#### Step 3: Redeploy via Bicep

```bash
make deploy-infra
```

This recreates the cluster with `nodeResourceGroup: rg-tola-infra-advisor-ai-nodes`.

#### Step 4: Get credentials and recreate secrets

```bash
az aks get-credentials \
  --resource-group rg-tola-infra-advisor-ai \
  --name aks-infra-advisor-dev \
  --overwrite-existing

make create-ghcr-secret

kubectl create namespace datadog
kubectl create secret generic datadog-secret \
  --namespace datadog \
  --from-literal=api-key=$DD_API_KEY \
  --from-literal=app-key=$DD_APP_KEY \
  --from-literal=site=datadoghq.com \
  --from-literal=cluster-agent-auth-token=$DD_CLUSTER_AGENT_TOKEN
```

#### Step 5: Redeploy all K8s manifests

```bash
make deploy-k8s
kubectl get pods -n infra-advisor --watch
```

#### Step 6: Update Cloudflare DNS with new LoadBalancer IP

```bash
# Wait for new LB IP
kubectl get svc ui -n infra-advisor --watch

# Once EXTERNAL-IP appears:
NEW_IP=$(kubectl get svc ui -n infra-advisor -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
echo "Update Cloudflare A record: infra-advisor-ai → $NEW_IP"
```

Update the A record in Cloudflare DNS dashboard, then verify:
```bash
curl -s https://infra-advisor-ai.kyletaylor.dev/api/health
```

---

## Final state after both parts

### `rg-tola-infra-advisor-ai` — only Bicep-managed resources remain

| Resource | Managed by |
|---|---|
| `aks-infra-advisor-dev` | `infra/bicep/modules/aks.bicep` |
| `oai-infra-advisor-dev` | `infra/bicep/modules/azure-openai.bicep` |
| `srch-infra-advisor-dev` | `infra/bicep/modules/azure-ai-search.bicep` |
| `law-infra-advisor-dev` | `infra/bicep/modules/monitoring.bicep` |

### `rg-tola-infra-advisor-ai-nodes` — AKS node infrastructure (Azure-managed)

Replaces the `MC_...` group. Contains VMs, NICs, LB, and public IPs for the cluster nodes.

---

## Recommendation

**Do Part A first** — it has no downtime, removes two unused paid resources (OpenAI S0 + Search Basic
SKU), and brings the live app under full Bicep management. Estimated savings: ~$150–200/month.

**Part B is optional** — the node RG name is an aesthetic issue only. Do it if a clean resource
group list matters for your organization, but schedule a maintenance window first.
