.PHONY: deploy-infra deploy-k8s check-env create-ghcr-secret create-airflow-secret create-mcp-server-secret create-agent-api-secret create-load-generator-secret create-postgres-secret create-auth-api-secret create-dd-postgres-secret create-secrets setup-postgres-dbm run-dags apply-datadog-agent upgrade-airflow help

# Load .env if present (for local dev)
-include .env
export

RESOURCE_GROUP ?= rg-tola-infra-advisor-ai
AKS_NAME ?= aks-infra-advisor-dev
LOCATION ?= eastus
NAMESPACE ?= infra-advisor
GHCR_PAT ?=
GITHUB_EMAIL ?=

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

check-env: ## Verify all required env vars are set before deploying
	@echo "→ Checking required environment variables..."
	@for var in \
		AZURE_OPENAI_ENDPOINT AZURE_OPENAI_API_KEY \
		AZURE_SEARCH_ENDPOINT AZURE_SEARCH_API_KEY \
		EIA_API_KEY DD_API_KEY \
		GHCR_PAT GITHUB_EMAIL \
		POSTGRES_USER POSTGRES_PASSWORD POSTGRES_DB \
		DD_POSTGRES_PASSWORD \
		DATABASE_URL JWT_SECRET; do \
		if [ -z "$$(eval echo \$$$$var)" ]; then \
			echo "  ERROR: $$var is not set"; \
			MISSING=1; \
		else \
			echo "  ✓ $$var"; \
		fi; \
	done; \
	if [ -n "$$MISSING" ]; then echo ""; echo "Set missing vars in .env and re-run."; exit 1; fi
	@echo "✓ All required env vars present"

# ─── Azure Infrastructure ──────────────────────────────────────────────────────

deploy-infra: ## Deploy Azure Bicep IaC (AKS, AI Search, OpenAI, etc.)
	@echo "→ Deploying Azure infrastructure (subscription-scoped)..."
	az deployment sub create \
		--location $(LOCATION) \
		--template-file infra/bicep/main.bicep \
		--parameters infra/bicep/parameters/dev.bicepparam \
		--verbose
	@echo "✓ Azure infrastructure deployed"

get-credentials: ## Fetch AKS kubeconfig
	az aks get-credentials \
		--resource-group $(RESOURCE_GROUP) \
		--name $(AKS_NAME) \
		--overwrite-existing
	@echo "✓ kubeconfig updated"

# ─── Kubernetes ───────────────────────────────────────────────────────────────

create-airflow-secret: ## Create airflow-azure-secret K8s Secret in airflow namespace
	@if [ -z "$(AZURE_OPENAI_ENDPOINT)" ]; then echo "ERROR: AZURE_OPENAI_ENDPOINT is not set"; exit 1; fi
	@if [ -z "$(EIA_API_KEY)" ]; then echo "ERROR: EIA_API_KEY is not set"; exit 1; fi
	@if [ -z "$(DD_API_KEY)" ]; then echo "ERROR: DD_API_KEY is not set (required for DJM OpenLineage transport)"; exit 1; fi
	@if [ -z "$(AIRFLOW_WEBSERVER_SECRET_KEY)" ]; then echo "ERROR: AIRFLOW_WEBSERVER_SECRET_KEY is not set — generate with: python3 -c \"import secrets; print(secrets.token_hex(32))\""; exit 1; fi
	kubectl create secret generic airflow-azure-secret \
		--namespace airflow \
		--from-literal=AZURE_OPENAI_ENDPOINT=$(AZURE_OPENAI_ENDPOINT) \
		--from-literal=AZURE_OPENAI_API_KEY=$(AZURE_OPENAI_API_KEY) \
		--from-literal=AZURE_SEARCH_ENDPOINT=$(AZURE_SEARCH_ENDPOINT) \
		--from-literal=AZURE_SEARCH_API_KEY=$(AZURE_SEARCH_API_KEY) \
		--from-literal=AZURE_STORAGE_CONNECTION_STRING="DefaultEndpointsProtocol=https;AccountName=placeholder;AccountKey=placeholder;EndpointSuffix=core.windows.net" \
		--from-literal=EIA_API_KEY=$(EIA_API_KEY) \
		--from-literal=DD_API_KEY=$(DD_API_KEY) \
		--from-literal=webserver-secret-key=$(AIRFLOW_WEBSERVER_SECRET_KEY) \
		--dry-run=client -o yaml | kubectl apply -f -
	@echo "✓ airflow-azure-secret created in namespace airflow"

create-mcp-server-secret: ## Create mcp-server-secret K8s Secret (Azure, EIA, ERCOT, SAM.gov, Tavily keys)
	@if [ -z "$(AZURE_SEARCH_ENDPOINT)" ];  then echo "ERROR: AZURE_SEARCH_ENDPOINT is not set";  exit 1; fi
	@if [ -z "$(AZURE_SEARCH_API_KEY)" ];   then echo "ERROR: AZURE_SEARCH_API_KEY is not set";   exit 1; fi
	@if [ -z "$(AZURE_OPENAI_ENDPOINT)" ];  then echo "ERROR: AZURE_OPENAI_ENDPOINT is not set";  exit 1; fi
	@if [ -z "$(AZURE_OPENAI_API_KEY)" ];   then echo "ERROR: AZURE_OPENAI_API_KEY is not set";   exit 1; fi
	@if [ -z "$(EIA_API_KEY)" ];            then echo "ERROR: EIA_API_KEY is not set";            exit 1; fi
	@if [ -z "$(ERCOT_API_KEY)" ];          then echo "WARN: ERCOT_API_KEY is not set — ERCOT tool will be disabled"; fi
	@if [ -z "$(SAMGOV_API_KEY)" ];         then echo "WARN: SAMGOV_API_KEY is not set — procurement opportunities tool will be disabled"; fi
	@if [ -z "$(TAVILY_API_KEY)" ];         then echo "WARN: TAVILY_API_KEY is not set — web procurement search tool will be disabled"; fi
	kubectl create secret generic mcp-server-secret \
		--namespace $(NAMESPACE) \
		--from-literal=AZURE_SEARCH_ENDPOINT=$(AZURE_SEARCH_ENDPOINT) \
		--from-literal=AZURE_SEARCH_API_KEY=$(AZURE_SEARCH_API_KEY) \
		--from-literal=AZURE_OPENAI_ENDPOINT=$(AZURE_OPENAI_ENDPOINT) \
		--from-literal=AZURE_OPENAI_API_KEY=$(AZURE_OPENAI_API_KEY) \
		--from-literal=EIA_API_KEY=$(EIA_API_KEY) \
		--from-literal=ERCOT_API_KEY=$(ERCOT_API_KEY) \
		--from-literal=SAMGOV_API_KEY=$(SAMGOV_API_KEY) \
		--from-literal=TAVILY_API_KEY=$(TAVILY_API_KEY) \
		--dry-run=client -o yaml | kubectl apply -f -
	@echo "✓ mcp-server-secret created in namespace $(NAMESPACE)"

create-agent-api-secret: ## Create agent-api-secret K8s Secret (Azure OpenAI keys)
	@if [ -z "$(AZURE_OPENAI_ENDPOINT)" ]; then echo "ERROR: AZURE_OPENAI_ENDPOINT is not set"; exit 1; fi
	@if [ -z "$(AZURE_OPENAI_API_KEY)" ];  then echo "ERROR: AZURE_OPENAI_API_KEY is not set";  exit 1; fi
	kubectl create secret generic agent-api-secret \
		--namespace $(NAMESPACE) \
		--from-literal=AZURE_OPENAI_ENDPOINT=$(AZURE_OPENAI_ENDPOINT) \
		--from-literal=AZURE_OPENAI_API_KEY=$(AZURE_OPENAI_API_KEY) \
		--dry-run=client -o yaml | kubectl apply -f -
	@echo "✓ agent-api-secret created in namespace $(NAMESPACE)"

create-load-generator-secret: ## Create load-generator-secret K8s Secret (Datadog API key)
	@if [ -z "$(DD_API_KEY)" ]; then echo "ERROR: DD_API_KEY is not set"; exit 1; fi
	kubectl create secret generic load-generator-secret \
		--namespace $(NAMESPACE) \
		--from-literal=DD_API_KEY=$(DD_API_KEY) \
		--dry-run=client -o yaml | kubectl apply -f -
	@echo "✓ load-generator-secret created in namespace $(NAMESPACE)"

create-postgres-secret: ## Create postgres-secret K8s Secret
	@if [ -z "$(POSTGRES_USER)" ]; then echo "ERROR: POSTGRES_USER is not set"; exit 1; fi
	@if [ -z "$(POSTGRES_PASSWORD)" ]; then echo "ERROR: POSTGRES_PASSWORD is not set"; exit 1; fi
	@if [ -z "$(POSTGRES_DB)" ]; then echo "ERROR: POSTGRES_DB is not set"; exit 1; fi
	kubectl create secret generic postgres-secret \
		--namespace $(NAMESPACE) \
		--from-literal=POSTGRES_USER=$(POSTGRES_USER) \
		--from-literal=POSTGRES_PASSWORD=$(POSTGRES_PASSWORD) \
		--from-literal=POSTGRES_DB=$(POSTGRES_DB) \
		--dry-run=client -o yaml | kubectl apply -f -
	@echo "✓ postgres-secret created"

create-auth-api-secret: ## Create auth-api-secret K8s Secret (DATABASE_URL, JWT_SECRET)
	@if [ -z "$(DATABASE_URL)" ]; then echo "ERROR: DATABASE_URL is not set"; exit 1; fi
	@if [ -z "$(JWT_SECRET)" ]; then echo "ERROR: JWT_SECRET is not set"; exit 1; fi
	kubectl create secret generic auth-api-secret \
		--namespace $(NAMESPACE) \
		--from-literal=DATABASE_URL=$(DATABASE_URL) \
		--from-literal=JWT_SECRET=$(JWT_SECRET) \
		--dry-run=client -o yaml | kubectl apply -f -
	@echo "✓ auth-api-secret created"

create-dd-postgres-secret: ## Create dd-postgres-secret K8s Secret (Datadog monitoring user password)
	@if [ -z "$(DD_POSTGRES_PASSWORD)" ]; then echo "ERROR: DD_POSTGRES_PASSWORD is not set"; exit 1; fi
	kubectl create secret generic dd-postgres-secret \
		--namespace $(NAMESPACE) \
		--from-literal=DD_POSTGRES_PASSWORD=$(DD_POSTGRES_PASSWORD) \
		--dry-run=client -o yaml | kubectl apply -f -
	@echo "✓ dd-postgres-secret created"

create-secrets: create-mcp-server-secret create-agent-api-secret create-load-generator-secret create-postgres-secret create-auth-api-secret create-dd-postgres-secret create-airflow-secret ## Create all application K8s secrets

setup-postgres-dbm: ## Create Datadog monitoring user + grants in Postgres (run once after deploy)
	@if [ -z "$(POSTGRES_USER)" ]; then echo "ERROR: POSTGRES_USER is not set"; exit 1; fi
	@if [ -z "$(DD_POSTGRES_PASSWORD)" ]; then echo "ERROR: DD_POSTGRES_PASSWORD is not set"; exit 1; fi
	chmod +x k8s/postgres/setup-dbm.sh
	NAMESPACE=$(NAMESPACE) POSTGRES_USER=$(POSTGRES_USER) POSTGRES_DB=$(POSTGRES_DB) \
		DD_POSTGRES_PASSWORD='$(DD_POSTGRES_PASSWORD)' \
		bash k8s/postgres/setup-dbm.sh

create-ghcr-secret: ## Create ghcr-pull-secret K8s Secret in infra-advisor namespace
	@if [ -z "$(GHCR_PAT)" ]; then echo "ERROR: GHCR_PAT is not set"; exit 1; fi
	@if [ -z "$(GITHUB_EMAIL)" ]; then echo "ERROR: GITHUB_EMAIL is not set"; exit 1; fi
	kubectl create secret docker-registry ghcr-pull-secret \
		--namespace $(NAMESPACE) \
		--docker-server=ghcr.io \
		--docker-username=kyletaylored \
		--docker-password=$(GHCR_PAT) \
		--docker-email=$(GITHUB_EMAIL) \
		--dry-run=client -o yaml | kubectl apply -f -
	@echo "✓ ghcr-pull-secret created in namespace $(NAMESPACE)"

deploy-k8s: check-env ## Apply all Kubernetes manifests
	@echo "→ Applying namespaces..."
	kubectl apply -f k8s/namespace.yaml

	@echo "→ Installing Strimzi CRDs..."
	kubectl apply -f https://strimzi.io/install/latest?namespace=kafka || true
	@echo "  Waiting for Strimzi CRDs to be established..."
	kubectl wait --for=condition=established crd/kafkas.kafka.strimzi.io --timeout=90s
	kubectl wait --for=condition=established crd/kafkatopics.kafka.strimzi.io --timeout=30s

	@echo "→ Skipping k8s/datadog/ — Datadog deployed via Operator (datadog/datadog-agent.yaml)"

	@echo "→ Deploying Kafka (Strimzi)..."
	kubectl apply -f k8s/kafka/

	@echo "→ Deploying Redis..."
	kubectl apply -f k8s/redis/

	@echo "→ Creating Airflow Azure secret..."
	$(MAKE) create-airflow-secret

	@echo "→ Deploying Airflow..."
	helm repo add apache-airflow https://airflow.apache.org || true
	helm repo update
	helm upgrade --install airflow apache-airflow/airflow \
		--namespace airflow \
		--values k8s/airflow/values.yaml \
		--timeout 10m \
		--wait

	@echo "→ Creating GHCR pull secret..."
	$(MAKE) create-ghcr-secret

	@echo "→ Creating application secrets..."
	$(MAKE) create-mcp-server-secret
	$(MAKE) create-agent-api-secret
	$(MAKE) create-load-generator-secret
	$(MAKE) create-postgres-secret
	$(MAKE) create-auth-api-secret
	$(MAKE) create-dd-postgres-secret

	@echo "→ Deploying Postgres..."
	kubectl apply -f k8s/postgres/

	@echo "→ Deploying application services..."
	kubectl apply -f k8s/mcp-server/
	kubectl apply -f k8s/agent-api/
	kubectl apply -f k8s/auth-api/
	kubectl apply -f k8s/load-generator/
	kubectl apply -f k8s/ui/

	@echo "✓ All Kubernetes resources applied"

rollout-status: ## Check rollout status for all infra-advisor deployments
	kubectl rollout status deploy/mcp-server -n $(NAMESPACE) --timeout=5m &
	kubectl rollout status deploy/agent-api -n $(NAMESPACE) --timeout=5m &
	wait
	@echo "✓ All deployments ready"

# ─── Airflow DAGs ─────────────────────────────────────────────────────────────

run-dags: ## Manually trigger all 5 Airflow DAGs
	@echo "→ Triggering knowledge_base_init DAG..."
	kubectl exec -n airflow deploy/airflow-scheduler -- airflow dags trigger knowledge_base_init
	@echo "→ Triggering nbi_refresh DAG..."
	kubectl exec -n airflow deploy/airflow-scheduler -- airflow dags trigger nbi_refresh
	@echo "→ Triggering fema_refresh DAG..."
	kubectl exec -n airflow deploy/airflow-scheduler -- airflow dags trigger fema_refresh
	@echo "→ Triggering eia_refresh DAG..."
	kubectl exec -n airflow deploy/airflow-scheduler -- airflow dags trigger eia_refresh
	@echo "→ Triggering twdb_water_plan_refresh DAG..."
	kubectl exec -n airflow deploy/airflow-scheduler -- airflow dags trigger twdb_water_plan_refresh
	@echo "✓ All DAGs triggered — check Airflow UI at http://localhost:8080 (after port-forward)"

airflow-ui: ## Port-forward Airflow web UI to localhost:8080
	kubectl port-forward -n airflow svc/airflow-api-server 8080:8080

apply-datadog-agent: ## Apply DatadogAgent CR from datadog/datadog-agent.yaml
	kubectl apply -f datadog/datadog-agent.yaml
	@echo "✓ DatadogAgent CR applied"

upgrade-airflow: ## Upgrade Airflow Helm release from k8s/airflow/values.yaml
	helm repo add apache-airflow https://airflow.apache.org || true
	helm repo update
	@STATUS=$$(helm status airflow -n airflow -o json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)['info']['status'])" 2>/dev/null || echo "not-found"); \
	if [ "$$STATUS" = "pending-install" ] || [ "$$STATUS" = "pending-upgrade" ] || [ "$$STATUS" = "pending-rollback" ]; then \
		echo "Release stuck in $$STATUS — rolling back to clear the lock"; \
		helm rollback airflow 0 --namespace airflow || true; \
	fi
	helm upgrade airflow apache-airflow/airflow \
		--namespace airflow \
		--values k8s/airflow/values.yaml \
		--timeout 10m \
		--wait \
		--cleanup-on-fail \
		--atomic
	@echo "✓ Airflow upgraded"

# ─── Tests ────────────────────────────────────────────────────────────────────

test-mcp: ## Run MCP server tests
	uv run pytest -x services/mcp-server/tests/

test-agent: ## Run agent API tests
	uv run pytest -x services/agent-api/tests/

test-load-gen: ## Run load generator tests
	uv run pytest -x services/load-generator/tests/

test-all: test-mcp test-agent test-load-gen ## Run all service tests

# ─── Docker ───────────────────────────────────────────────────────────────────

GHCR_PREFIX ?= ghcr.io/kyletaylored/infra-advisor-ai
IMAGE_TAG ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo "local")

docker-build-mcp: ## Build MCP server image
	docker build -t $(GHCR_PREFIX)/mcp-server:$(IMAGE_TAG) services/mcp-server/

docker-build-agent: ## Build agent API image
	docker build -t $(GHCR_PREFIX)/agent-api:$(IMAGE_TAG) services/agent-api/

docker-build-load-gen: ## Build load generator image
	docker build -t $(GHCR_PREFIX)/load-generator:$(IMAGE_TAG) services/load-generator/

docker-build-ui: ## Build UI image
	docker build -t $(GHCR_PREFIX)/ui:$(IMAGE_TAG) services/ui/

docker-build-all: docker-build-mcp docker-build-agent docker-build-load-gen docker-build-ui ## Build all images

docker-push-all: ## Push all images to GHCR
	docker push $(GHCR_PREFIX)/mcp-server:$(IMAGE_TAG)
	docker push $(GHCR_PREFIX)/agent-api:$(IMAGE_TAG)
	docker push $(GHCR_PREFIX)/load-generator:$(IMAGE_TAG)
	docker push $(GHCR_PREFIX)/ui:$(IMAGE_TAG)

# ─── Verification ─────────────────────────────────────────────────────────────

check-pods: ## Check pod status across all namespaces
	@echo "=== infra-advisor ==="
	kubectl get pods -n infra-advisor
	@echo ""
	@echo "=== kafka ==="
	kubectl get pods -n kafka
	@echo ""
	@echo "=== airflow ==="
	kubectl get pods -n airflow
	@echo ""
	@echo "=== datadog ==="
	kubectl get pods -n datadog

check-nodes: ## Check AKS node status
	kubectl get nodes -o wide

logs-mcp: ## Tail MCP server logs
	kubectl logs -n $(NAMESPACE) deploy/mcp-server --tail=50 -f

logs-agent: ## Tail agent API logs
	kubectl logs -n $(NAMESPACE) deploy/agent-api --tail=50 -f
