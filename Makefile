.PHONY: deploy-infra deploy-k8s check-env create-ghcr-secret create-airflow-secret create-mcp-server-secret create-mcp-server-dotnet-secret create-agent-api-secret create-agent-api-dotnet-secret create-load-generator-secret create-postgres-secret create-redis-secret create-auth-api-secret create-dd-postgres-secret create-mailpit-secret create-secrets redeploy-mailpit setup-postgres-dbm run-dags apply-datadog-agent install-airflow upgrade-airflow sync-dags otel-poc run-otel-poc build-otel-poc otel-maf-poc run-otel-maf-poc build-otel-maf-poc start-otel-collector stop-otel-collector logs-otel-collector help

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
		DATABASE_URL JWT_SECRET \
		AIRFLOW_ADMIN_USERNAME AIRFLOW_ADMIN_PASSWORD \
		MAILPIT_UI_USERNAME MAILPIT_UI_PASSWORD; do \
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

create-mcp-server-secret: ## Create mcp-server-secret K8s Secret (Azure, EIA, ERCOT, SAM.gov keys)
	@if [ -z "$(AZURE_SEARCH_ENDPOINT)" ];  then echo "ERROR: AZURE_SEARCH_ENDPOINT is not set";  exit 1; fi
	@if [ -z "$(AZURE_SEARCH_API_KEY)" ];   then echo "ERROR: AZURE_SEARCH_API_KEY is not set";   exit 1; fi
	@if [ -z "$(AZURE_OPENAI_ENDPOINT)" ];  then echo "ERROR: AZURE_OPENAI_ENDPOINT is not set";  exit 1; fi
	@if [ -z "$(AZURE_OPENAI_API_KEY)" ];   then echo "ERROR: AZURE_OPENAI_API_KEY is not set";   exit 1; fi
	@if [ -z "$(EIA_API_KEY)" ];            then echo "ERROR: EIA_API_KEY is not set";            exit 1; fi
	@if [ -z "$(ERCOT_API_KEY)" ];          then echo "WARN: ERCOT_API_KEY is not set — ERCOT tool will be disabled"; fi
	@if [ -z "$(SAMGOV_API_KEY)" ];         then echo "WARN: SAMGOV_API_KEY is not set — procurement opportunities tool will be disabled"; fi
	kubectl create secret generic mcp-server-secret \
		--namespace $(NAMESPACE) \
		--from-literal=AZURE_SEARCH_ENDPOINT=$(AZURE_SEARCH_ENDPOINT) \
		--from-literal=AZURE_SEARCH_API_KEY=$(AZURE_SEARCH_API_KEY) \
		--from-literal=AZURE_OPENAI_ENDPOINT=$(AZURE_OPENAI_ENDPOINT) \
		--from-literal=AZURE_OPENAI_API_KEY=$(AZURE_OPENAI_API_KEY) \
		--from-literal=EIA_API_KEY=$(EIA_API_KEY) \
		--from-literal=ERCOT_API_KEY=$(ERCOT_API_KEY) \
		--from-literal=SAMGOV_API_KEY=$(SAMGOV_API_KEY) \
		--dry-run=client -o yaml | kubectl apply -f -
	@echo "✓ mcp-server-secret created in namespace $(NAMESPACE)"

create-mcp-server-dotnet-secret: ## Create mcp-server-dotnet-secret K8s Secret (Azure Search + OpenAI + optional API keys)
	@if [ -z "$(AZURE_SEARCH_ENDPOINT)" ];  then echo "ERROR: AZURE_SEARCH_ENDPOINT is not set";  exit 1; fi
	@if [ -z "$(AZURE_SEARCH_API_KEY)" ];   then echo "ERROR: AZURE_SEARCH_API_KEY is not set";   exit 1; fi
	@if [ -z "$(AZURE_OPENAI_ENDPOINT)" ];  then echo "ERROR: AZURE_OPENAI_ENDPOINT is not set";  exit 1; fi
	@if [ -z "$(AZURE_OPENAI_API_KEY)" ];   then echo "ERROR: AZURE_OPENAI_API_KEY is not set";   exit 1; fi
	@if [ -z "$(EIA_API_KEY)" ];            then echo "WARN: EIA_API_KEY is not set — EIA tool will be disabled"; fi
	@if [ -z "$(ERCOT_API_KEY)" ];          then echo "WARN: ERCOT_API_KEY is not set — ERCOT tool will be disabled"; fi
	@if [ -z "$(SAMGOV_API_KEY)" ];         then echo "WARN: SAMGOV_API_KEY is not set — SAM.gov tool will be disabled"; fi
	kubectl create secret generic mcp-server-dotnet-secret \
		--namespace $(NAMESPACE) \
		--from-literal=AZURE_SEARCH_ENDPOINT=$(AZURE_SEARCH_ENDPOINT) \
		--from-literal=AZURE_SEARCH_API_KEY=$(AZURE_SEARCH_API_KEY) \
		--from-literal=AZURE_OPENAI_ENDPOINT=$(AZURE_OPENAI_ENDPOINT) \
		--from-literal=AZURE_OPENAI_API_KEY=$(AZURE_OPENAI_API_KEY) \
		$(if $(EIA_API_KEY),--from-literal=EIA_API_KEY=$(EIA_API_KEY),) \
		$(if $(ERCOT_API_KEY),--from-literal=ERCOT_API_KEY=$(ERCOT_API_KEY),) \
		$(if $(SAMGOV_API_KEY),--from-literal=SAMGOV_API_KEY=$(SAMGOV_API_KEY),) \
		--dry-run=client -o yaml | kubectl apply -f -
	@echo "✓ mcp-server-dotnet-secret created in namespace $(NAMESPACE)"

create-agent-api-secret: ## Create agent-api-secret K8s Secret (Azure OpenAI keys + DATABASE_URL + JWT_SECRET + DD_API_KEY/DD_APP_KEY for AI Guard)
	@if [ -z "$(AZURE_OPENAI_ENDPOINT)" ]; then echo "ERROR: AZURE_OPENAI_ENDPOINT is not set"; exit 1; fi
	@if [ -z "$(AZURE_OPENAI_API_KEY)" ];  then echo "ERROR: AZURE_OPENAI_API_KEY is not set";  exit 1; fi
	@if [ -z "$(JWT_SECRET)" ]; then echo "ERROR: JWT_SECRET is not set (shared with auth-api for /query auth)"; exit 1; fi
	@if [ -z "$(DATABASE_URL)" ]; then echo "WARN: DATABASE_URL not set — conversation persistence will be disabled"; fi
	@if [ -z "$(DD_API_KEY)" ] || [ -z "$(DD_APP_KEY)" ]; then echo "WARN: DD_API_KEY/DD_APP_KEY not both set — AI Guard LangChain auto-integration will be disabled"; fi
	kubectl create secret generic agent-api-secret \
		--namespace $(NAMESPACE) \
		--from-literal=AZURE_OPENAI_ENDPOINT=$(AZURE_OPENAI_ENDPOINT) \
		--from-literal=AZURE_OPENAI_API_KEY=$(AZURE_OPENAI_API_KEY) \
		--from-literal=JWT_SECRET=$(JWT_SECRET) \
		$(if $(DATABASE_URL),--from-literal=DATABASE_URL=$(DATABASE_URL),) \
		$(if $(DD_API_KEY),--from-literal=DD_API_KEY=$(DD_API_KEY),) \
		$(if $(DD_APP_KEY),--from-literal=DD_APP_KEY=$(DD_APP_KEY),) \
		--dry-run=client -o yaml | kubectl apply -f -
	@echo "✓ agent-api-secret created in namespace $(NAMESPACE)"

create-agent-api-dotnet-secret: ## Create agent-api-dotnet-secret K8s Secret (Azure OpenAI keys + DATABASE_URL + DD_API_KEY + DD_APPLICATION_KEY + JWT_SECRET)
	@if [ -z "$(AZURE_OPENAI_ENDPOINT)" ]; then echo "ERROR: AZURE_OPENAI_ENDPOINT is not set"; exit 1; fi
	@if [ -z "$(AZURE_OPENAI_API_KEY)" ];  then echo "ERROR: AZURE_OPENAI_API_KEY is not set";  exit 1; fi
	@if [ -z "$(JWT_SECRET)" ]; then echo "ERROR: JWT_SECRET is not set (shared with auth-api for /query auth)"; exit 1; fi
	@if [ -z "$(DD_API_KEY)" ];            then echo "WARN: DD_API_KEY not set — LLM Observability OTLP export will be disabled"; fi
	@if [ -z "$(DD_APPLICATION_KEY)" ];    then echo "WARN: DD_APPLICATION_KEY not set — AI Guard HTTP API calls will be disabled"; fi
	@if [ -z "$(DATABASE_URL)" ]; then echo "WARN: DATABASE_URL not set — conversation persistence will be disabled"; fi
	kubectl create secret generic agent-api-dotnet-secret \
		--namespace $(NAMESPACE) \
		--from-literal=AZURE_OPENAI_ENDPOINT=$(AZURE_OPENAI_ENDPOINT) \
		--from-literal=AZURE_OPENAI_API_KEY=$(AZURE_OPENAI_API_KEY) \
		--from-literal=JWT_SECRET=$(JWT_SECRET) \
		$(if $(DD_API_KEY),--from-literal=DD_API_KEY=$(DD_API_KEY),) \
		$(if $(DD_APPLICATION_KEY),--from-literal=DD_APPLICATION_KEY=$(DD_APPLICATION_KEY),) \
		$(if $(DATABASE_URL),--from-literal=DATABASE_URL=$(DATABASE_URL),) \
		--dry-run=client -o yaml | kubectl apply -f -
	@echo "✓ agent-api-dotnet-secret created in namespace $(NAMESPACE)"

create-load-generator-secret: ## Create load-generator-secret K8s Secret (Datadog API key)
	@if [ -z "$(DD_API_KEY)" ]; then echo "ERROR: DD_API_KEY is not set"; exit 1; fi
	kubectl create secret generic load-generator-secret \
		--namespace $(NAMESPACE) \
		--from-literal=DD_API_KEY=$(DD_API_KEY) \
		--dry-run=client -o yaml | kubectl apply -f -
	@echo "✓ load-generator-secret created in namespace $(NAMESPACE)"

create-redis-secret: ## Create redis-secret K8s Secret (REDIS_PASSWORD)
	@if [ -z "$(REDIS_PASSWORD)" ]; then echo "ERROR: REDIS_PASSWORD is not set — generate with: openssl rand -base64 24"; exit 1; fi
	kubectl create secret generic redis-secret \
		--namespace $(NAMESPACE) \
		--from-literal=REDIS_PASSWORD=$(REDIS_PASSWORD) \
		--dry-run=client -o yaml | kubectl apply -f -
	@echo "✓ redis-secret created"

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

create-auth-api-secret: ## Create auth-api-secret K8s Secret (DATABASE_URL, JWT_SECRET, optional bootstrap admin)
	@if [ -z "$(DATABASE_URL)" ]; then echo "ERROR: DATABASE_URL is not set"; exit 1; fi
	@if [ -z "$(JWT_SECRET)" ]; then echo "ERROR: JWT_SECRET is not set"; exit 1; fi
	@if [ -z "$(BOOTSTRAP_ADMIN_EMAIL)" ] || [ -z "$(BOOTSTRAP_ADMIN_PASSWORD)" ]; then \
		echo "WARN: BOOTSTRAP_ADMIN_EMAIL/PASSWORD not set — auth-api will start without a bootstrap admin"; \
		echo "      (existing admin users keep working; only matters on a fresh DB)"; \
	fi
	kubectl create secret generic auth-api-secret \
		--namespace $(NAMESPACE) \
		--from-literal=DATABASE_URL=$(DATABASE_URL) \
		--from-literal=JWT_SECRET=$(JWT_SECRET) \
		$(if $(BOOTSTRAP_ADMIN_EMAIL),--from-literal=BOOTSTRAP_ADMIN_EMAIL=$(BOOTSTRAP_ADMIN_EMAIL),) \
		$(if $(BOOTSTRAP_ADMIN_PASSWORD),--from-literal=BOOTSTRAP_ADMIN_PASSWORD=$(BOOTSTRAP_ADMIN_PASSWORD),) \
		--dry-run=client -o yaml | kubectl apply -f -
	@echo "✓ auth-api-secret created"

create-dd-postgres-secret: ## Create dd-postgres-secret K8s Secret in datadog namespace (referenced by DatadogAgent CR)
	@if [ -z "$(DD_POSTGRES_PASSWORD)" ]; then echo "ERROR: DD_POSTGRES_PASSWORD is not set"; exit 1; fi
	kubectl create secret generic dd-postgres-secret \
		--namespace datadog \
		--from-literal=DD_POSTGRES_PASSWORD=$(DD_POSTGRES_PASSWORD) \
		--dry-run=client -o yaml | kubectl apply -f -
	@echo "✓ dd-postgres-secret created in namespace datadog"

create-mailpit-secret: ## Create mailpit-secret with bcrypt-hashed MP_UI_AUTH for the inbox web UI
	@if [ -z "$(MAILPIT_UI_USERNAME)" ]; then echo "ERROR: MAILPIT_UI_USERNAME is not set"; exit 1; fi
	@if [ -z "$(MAILPIT_UI_PASSWORD)" ]; then echo "ERROR: MAILPIT_UI_PASSWORD is not set — generate one with: openssl rand -base64 24"; exit 1; fi
	@command -v htpasswd >/dev/null 2>&1 || { echo "ERROR: htpasswd not found — install apache2-utils (Debian) or httpd (macOS)"; exit 1; }
	@# `htpasswd -nbB` emits user:$2y$10$... — Mailpit's MP_UI_AUTH accepts the
	@# same bcrypt prefix variants ($2a / $2b / $2y), so no rewriting needed.
	@AUTH="$$(htpasswd -nbB -C 10 "$(MAILPIT_UI_USERNAME)" "$(MAILPIT_UI_PASSWORD)")"; \
	kubectl create secret generic mailpit-secret \
		--namespace $(NAMESPACE) \
		--from-literal=MP_UI_AUTH="$$AUTH" \
		--dry-run=client -o yaml | kubectl apply -f -
	@echo "✓ mailpit-secret created (user: $(MAILPIT_UI_USERNAME))"

create-secrets: create-mcp-server-secret create-mcp-server-dotnet-secret create-agent-api-secret create-agent-api-dotnet-secret create-load-generator-secret create-redis-secret create-postgres-secret create-auth-api-secret create-dd-postgres-secret create-airflow-secret create-mailpit-secret ## Create all application K8s secrets

redeploy-mailpit: ## Apply the Mailpit manifest, evict stuck pods from older ReplicaSets, wait for rollout, verify probe + endpoint
	@echo "→ Applying k8s/mailpit/deployment.yaml + service.yaml + configmap.yaml..."
	kubectl apply -f k8s/mailpit/
	@echo "→ Force-deleting any pods from older ReplicaSets so the new RS can roll fresh..."
	@# --force --grace-period=0 because stuck CrashLoopBackOff pods otherwise
	@# block the rollout when terminationGracePeriodSeconds is the default 30s
	@# and the kubelet is still bouncing them.
	kubectl delete pod -n $(NAMESPACE) -l app=mailpit --force --grace-period=0 2>/dev/null || true
	@echo "→ Waiting for rollout to reach Ready..."
	kubectl rollout status deploy/mailpit -n $(NAMESPACE) --timeout=2m
	@echo "→ Verifying the live readiness probe is tcpSocket (not httpGet)..."
	@PROBE=$$(kubectl get deploy mailpit -n $(NAMESPACE) -o jsonpath='{.spec.template.spec.containers[0].readinessProbe}'); \
	if echo "$$PROBE" | grep -q tcpSocket; then \
		echo "  ✓ readiness probe is tcpSocket"; \
	else \
		echo "  ✗ readiness probe is NOT tcpSocket — apply did not take effect"; \
		echo "    spec: $$PROBE"; \
		exit 1; \
	fi
	@echo "→ Verifying the Service has a healthy endpoint..."
	@EP=$$(kubectl get endpoints mailpit -n $(NAMESPACE) -o jsonpath='{.subsets[*].addresses[*].ip}'); \
	if [ -n "$$EP" ]; then \
		echo "  ✓ mailpit endpoint(s): $$EP"; \
	else \
		echo "  ✗ mailpit endpoint is empty — pod still not Ready"; \
		exit 1; \
	fi
	@echo "✓ Mailpit redeployed. Open https://infra-advisor-ai.kyletaylor.dev/mailpit/ (basic auth: $(MAILPIT_UI_USERNAME))"

setup-postgres-dbm: ## Create Datadog monitoring user + grants in Postgres (run once after deploy; requires authuser superuser)
	@if [ -z "$(DD_POSTGRES_PASSWORD)" ]; then echo "ERROR: DD_POSTGRES_PASSWORD is not set"; exit 1; fi
	chmod +x k8s/postgres/setup-dbm.sh
	NAMESPACE=$(NAMESPACE) \
		POSTGRES_USER=$${POSTGRES_USER:-authuser} \
		POSTGRES_DB=$${POSTGRES_DB:-postgres} \
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

	@echo "→ Deploying Mailpit (SMTP capture for dev)..."
	$(MAKE) create-mailpit-secret
	kubectl apply -f k8s/mailpit/

	@echo "→ Creating Airflow Azure secret..."
	$(MAKE) create-airflow-secret

	@echo "→ Deploying Airflow..."
	helm repo add apache-airflow https://airflow.apache.org || true
	helm repo update
	$(MAKE) install-airflow

	@echo "→ Creating GHCR pull secret..."
	$(MAKE) create-ghcr-secret

	@echo "→ Creating application secrets..."
	$(MAKE) create-mcp-server-secret
	$(MAKE) create-mcp-server-dotnet-secret
	$(MAKE) create-agent-api-secret
	$(MAKE) create-agent-api-dotnet-secret
	$(MAKE) create-load-generator-secret
	$(MAKE) create-postgres-secret
	$(MAKE) create-auth-api-secret
	$(MAKE) create-dd-postgres-secret

	@echo "→ Deploying Postgres..."
	kubectl apply -f k8s/postgres/

	@echo "→ Deploying application services..."
	kubectl apply -f k8s/mcp-server/
	kubectl apply -f k8s/mcp-server-dotnet/
	kubectl apply -f k8s/agent-api/
	kubectl apply -f k8s/agent-api-dotnet/
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
	kubectl exec -n airflow airflow-scheduler-0 -c scheduler -- airflow dags trigger knowledge_base_init
	@echo "→ Triggering nbi_refresh DAG..."
	kubectl exec -n airflow airflow-scheduler-0 -c scheduler -- airflow dags trigger nbi_refresh
	@echo "→ Triggering fema_refresh DAG..."
	kubectl exec -n airflow airflow-scheduler-0 -c scheduler -- airflow dags trigger fema_refresh
	@echo "→ Triggering eia_refresh DAG..."
	kubectl exec -n airflow airflow-scheduler-0 -c scheduler -- airflow dags trigger eia_refresh
	@echo "→ Triggering twdb_water_plan_refresh DAG..."
	kubectl exec -n airflow airflow-scheduler-0 -c scheduler -- airflow dags trigger twdb_water_plan_refresh
	@echo "✓ All DAGs triggered — check Airflow UI at https://infra-advisor-ai.kyletaylor.dev/airflow"

airflow-ui: ## Port-forward Airflow web UI to localhost:8080
	kubectl port-forward -n airflow svc/airflow-api-server 8080:8080

apply-datadog-agent: ## Apply DatadogAgent CR from datadog/datadog-agent.yaml
	kubectl apply -f datadog/datadog-agent.yaml
	@echo "✓ DatadogAgent CR applied"

install-airflow: ## Fresh install of Airflow (nukes existing release)
	@if [ -z "$(AIRFLOW_ADMIN_USERNAME)" ]; then echo "ERROR: AIRFLOW_ADMIN_USERNAME is not set (override Airflow admin user)"; exit 1; fi
	@if [ -z "$(AIRFLOW_ADMIN_PASSWORD)" ]; then echo "ERROR: AIRFLOW_ADMIN_PASSWORD is not set — generate one with: openssl rand -base64 32"; exit 1; fi
	helm repo add apache-airflow https://airflow.apache.org || true
	helm repo update
	-helm uninstall airflow -n airflow --no-hooks 2>/dev/null || true
	kubectl delete namespace airflow 2>/dev/null || true
	kubectl create namespace airflow
	$(MAKE) create-airflow-secret
	@echo "→ Installing Airflow (no wait — avoids post-install hook deadlock)..."
	helm install airflow apache-airflow/airflow \
		--namespace airflow \
		--values k8s/airflow/values.yaml \
		--set createUserJob.defaultUser.username='$(AIRFLOW_ADMIN_USERNAME)' \
		--set createUserJob.defaultUser.password='$(AIRFLOW_ADMIN_PASSWORD)' \
		--timeout 20m
	@echo "→ Waiting for PostgreSQL to be ready..."
	kubectl wait --for=condition=ready pod \
		-l app.kubernetes.io/name=postgresql \
		-n airflow --timeout=3m
	@echo "→ Running DB migration manually (chart hook unreliable on fresh install)..."
	kubectl run airflow-migrate \
		--image=apache/airflow:3.1.8 \
		--restart=Never \
		--namespace=airflow \
		--env="AIRFLOW__DATABASE__SQL_ALCHEMY_CONN=postgresql+psycopg2://postgres:postgres@airflow-postgresql:5432/postgres" \
		--env="AIRFLOW__CORE__FERNET_KEY=$$(kubectl get secret airflow-fernet-key -n airflow -o jsonpath='{.data.fernet-key}' | base64 -d)" \
		--env="AIRFLOW__API__SECRET_KEY=$$(kubectl get secret airflow-api-secret-key -n airflow -o jsonpath='{.data.api-secret-key}' | base64 -d)" \
		-- airflow db migrate
	@echo "→ Waiting for migration to complete..."
	kubectl wait --for=condition=ready pod/airflow-migrate \
		-n airflow --timeout=5m
	kubectl logs -n airflow airflow-migrate --follow
	kubectl delete pod airflow-migrate -n airflow
	@echo "→ Waiting for all Airflow pods to be ready..."
	kubectl wait --for=condition=ready pod \
		-l tier=airflow -n airflow \
		--timeout=15m \
		--field-selector=status.phase!=Succeeded
	@echo "✓ Airflow installed and ready"
	@echo "→ Creating admin user..."
	kubectl exec -n airflow airflow-scheduler-0 -- \
		airflow users create \
		--role Admin --username admin \
		--email admin@infra-advisor.local \
		--firstname Admin --lastname User \
		--password admin 2>/dev/null || true

upgrade-airflow: ## Upgrade Airflow Helm release from k8s/airflow/values.yaml
	helm repo add apache-airflow https://airflow.apache.org || true
	helm repo update
	@STATUS=$$(helm status airflow -n airflow -o json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)['info']['status'])" 2>/dev/null || echo "not-found"); \
	if [ "$$STATUS" = "pending-install" ] || [ "$$STATUS" = "pending-upgrade" ] || [ "$$STATUS" = "pending-rollback" ] || [ "$$STATUS" = "failed" ]; then \
		echo "Release in $$STATUS — rolling back to clear the lock"; \
		helm rollback airflow 0 --namespace airflow 2>/dev/null || helm uninstall airflow -n airflow --no-hooks 2>/dev/null || true; \
	fi
	@if [ -z "$(AIRFLOW_ADMIN_USERNAME)" ]; then echo "ERROR: AIRFLOW_ADMIN_USERNAME is not set"; exit 1; fi
	@if [ -z "$(AIRFLOW_ADMIN_PASSWORD)" ]; then echo "ERROR: AIRFLOW_ADMIN_PASSWORD is not set"; exit 1; fi
	-kubectl delete job airflow-create-user -n airflow --ignore-not-found=true 2>/dev/null
	helm upgrade airflow apache-airflow/airflow \
		--namespace airflow \
		--values k8s/airflow/values.yaml \
		--set createUserJob.defaultUser.username='$(AIRFLOW_ADMIN_USERNAME)' \
		--set createUserJob.defaultUser.password='$(AIRFLOW_ADMIN_PASSWORD)' \
		--timeout 20m \
		--cleanup-on-fail
	@echo "→ Waiting for migration job..."
	kubectl wait --for=condition=complete job/airflow-run-airflow-migrations \
		-n airflow --timeout=10m
	@echo "→ Waiting for pods..."
	kubectl wait --for=condition=ready pod \
		-l tier=airflow -n airflow --timeout=15m
	@echo "✓ Airflow upgraded"

sync-dags: ## Copy DAGs from repo to airflow-scheduler PVC
	@echo "→ Syncing DAGs to airflow PVC..."
	kubectl cp services/ingestion/dags/. \
		airflow/airflow-scheduler-0:/opt/airflow/dags/ \
		-c scheduler
	@echo "✓ DAGs synced — dag-processor will pick them up within 30s"

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

# ─── Experiments ──────────────────────────────────────────────────────────────

# Default port 5005 — macOS AirPlay Receiver hijacks :5000. Override with
# `make run-otel-poc OTEL_POC_PORT=7000` if 5005 is also in use.
OTEL_POC_PORT ?= 5005

otel-poc: ## Start collector + run POC (single entry point — Ctrl+C stops both)
	@$(MAKE) --no-print-directory start-otel-collector
	@echo ""
	@echo "▸ POC starting on http://localhost:$(OTEL_POC_PORT)"
	@echo "  Ctrl+C will stop the POC AND tear down the collector."
	@echo ""
	@# Trap fires on Ctrl+C (INT), kill (TERM), or normal/error exit (EXIT).
	@# Always runs `stop-otel-collector` so we never leave the container
	@# running when the foreground POC exits.
	@trap '$(MAKE) --no-print-directory stop-otel-collector' EXIT INT TERM; \
	$(MAKE) --no-print-directory run-otel-poc

run-otel-poc: ## Run the .NET OTel POC only (assumes collector already running)
	@# Shell-level $$VAR (not Make's $(VAR)) so secrets aren't expanded into
	@# the recipe text at parse time — keeps them out of `make -n` output.
	@if [ -z "$$AZURE_OPENAI_ENDPOINT" ] || [ -z "$$AZURE_OPENAI_API_KEY" ]; then \
		echo "ERROR: AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY must be set in root .env"; \
		exit 1; \
	fi
	@# The POC defaults to OTLP → http://localhost:4318 (local OTel Collector).
	@# Warn if nothing is listening there. Override OTEL_EXPORTER_OTLP_ENDPOINT
	@# to point at any other OTLP-compatible endpoint instead.
	@if [ -z "$$OTEL_EXPORTER_OTLP_ENDPOINT" ] && ! nc -z localhost 4318 2>/dev/null; then \
		echo "WARN: Nothing listening on localhost:4318 — telemetry will fail to export."; \
		echo "      Run `make start-otel-collector` first, OR set"; \
		echo "      OTEL_EXPORTER_OTLP_ENDPOINT to point at another OTLP endpoint."; \
		echo ""; \
	fi
	@echo "→ Starting .NET OTel POC on http://localhost:$(OTEL_POC_PORT)  (Ctrl+C to stop)"
	@echo "  OTLP target: $${OTEL_EXPORTER_OTLP_ENDPOINT:-http://localhost:4318}"
	@echo "  Service: $${OTEL_SERVICE_NAME:-otel-genai-poc}"
	@echo ""
	@# RUM env passthrough: the main UI's .env uses VITE_DD_RUM_APP_ID /
	@# VITE_DD_RUM_CLIENT_TOKEN. Map those onto the POC's expected
	@# DD_RUM_APPLICATION_ID / DD_RUM_CLIENT_TOKEN if the POC-prefixed
	@# names aren't explicitly set.
	@cd experiments/dotnet-otel-poc && \
		ASPNETCORE_URLS=http://localhost:$(OTEL_POC_PORT) \
		DD_RUM_APPLICATION_ID="$${DD_RUM_APPLICATION_ID:-$$VITE_DD_RUM_APP_ID}" \
		DD_RUM_CLIENT_TOKEN="$${DD_RUM_CLIENT_TOKEN:-$$VITE_DD_RUM_CLIENT_TOKEN}" \
		DD_SITE="$${DD_SITE:-$$VITE_DD_RUM_SITE}" \
		dotnet run

build-otel-poc: ## Build the .NET OTel POC without running (compile-check only)
	cd experiments/dotnet-otel-poc && dotnet build -c Release

# ─── MAF POC (Microsoft Agents Framework) ──────────────────────────────────────
# Mirrors the M.E.AI-only POC but on Microsoft.Agents.AI 1.5.0 — adds the
# invoke_agent span layer, AgentSession-based conversation grouping, and
# the AIContextProvider hook. Listens on a separate port (5007) so both
# POCs can run side-by-side against the same local collector.

OTEL_MAF_POC_PORT ?= 5007

otel-maf-poc: ## Start collector + run MAF POC (Ctrl+C stops both)
	@$(MAKE) --no-print-directory start-otel-collector
	@echo ""
	@echo "▸ MAF POC starting on http://localhost:$(OTEL_MAF_POC_PORT)  (Ctrl+C to stop)"
	@echo ""
	@trap '$(MAKE) --no-print-directory stop-otel-collector' EXIT INT TERM; \
	$(MAKE) --no-print-directory run-otel-maf-poc

run-otel-maf-poc: ## Run the MAF POC only (assumes collector already running)
	@if [ -z "$$AZURE_OPENAI_ENDPOINT" ] || [ -z "$$AZURE_OPENAI_API_KEY" ]; then \
		echo "ERROR: AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY must be set in root .env"; \
		exit 1; \
	fi
	@if [ -z "$$OTEL_EXPORTER_OTLP_ENDPOINT" ] && ! nc -z localhost 4318 2>/dev/null; then \
		echo "WARN: Nothing listening on localhost:4318 — telemetry will fail to export."; \
		echo "      Run `make start-otel-collector` first."; \
		echo ""; \
	fi
	@echo "→ Starting MAF POC on http://localhost:$(OTEL_MAF_POC_PORT)  (Ctrl+C to stop)"
	@echo "  OTLP target: $${OTEL_EXPORTER_OTLP_ENDPOINT:-http://localhost:4318}"
	@echo "  Service: $${OTEL_SERVICE_NAME:-infra-advisor-maf-poc}"
	@echo ""
	@# Hardcode MCP_SERVER_URL to the port-forwarded localhost — root .env
	@# has the cluster-internal MCP_SERVER_URL (used by the production
	@# agent-api), which isn't resolvable from the host. Override via
	@# MAF_POC_MCP_URL=... in your shell to point at any other target.
	@cd experiments/dotnet-maf-poc && \
		ASPNETCORE_URLS=http://localhost:$(OTEL_MAF_POC_PORT) \
		MCP_SERVER_URL="$${MAF_POC_MCP_URL:-http://localhost:8000/mcp}" \
		DD_RUM_APPLICATION_ID="$${DD_RUM_APPLICATION_ID:-$$VITE_DD_RUM_APP_ID}" \
		DD_RUM_CLIENT_TOKEN="$${DD_RUM_CLIENT_TOKEN:-$$VITE_DD_RUM_CLIENT_TOKEN}" \
		DD_SITE="$${DD_SITE:-$$VITE_DD_RUM_SITE}" \
		dotnet run

build-otel-maf-poc: ## Build the MAF POC without running (compile-check only)
	cd experiments/dotnet-maf-poc && dotnet build -c Release

start-otel-collector: ## Start local OTel Collector (Docker) on :4317 / :4318
	@if [ -z "$$DD_API_KEY" ]; then \
		echo "ERROR: DD_API_KEY must be set (root .env)"; exit 1; \
	fi
	cd experiments/otel-collector && docker compose up -d
	@echo ""
	@echo "✓ Collector running. Tail logs:  make logs-otel-collector"
	@echo "                     Stop it:     make stop-otel-collector"

stop-otel-collector: ## Stop local OTel Collector
	cd experiments/otel-collector && docker compose down

logs-otel-collector: ## Tail local OTel Collector logs (every span/metric/log body)
	docker logs -f otel-collector-poc
