# InfraAdvisor AI — agent context

Global infrastructure consulting firm AI assistant. See @docs/agent-guides/project-map.md.

## Build and verify commands
- `make deploy-infra` — apply Bicep IaC
- `make create-ghcr-secret` — create K8s imagePullSecret for GHCR (run before deploy-k8s)
- `make deploy-k8s` — apply all K8s manifests
- `uv run pytest -x services/<service>/tests/` — run tests for a service
- `kubectl get pods -n infra-advisor` — check pod status
- `kubectl logs -n infra-advisor deploy/<n> --tail=50` — check logs
- `az aks get-credentials --resource-group rg-tola-infra-advisor-ai --name aks-infra-advisor` — get kubeconfig

## Key constraints
- All Python services use `uv`, Python 3.12, `pyproject.toml`
- `import ddtrace.auto` must be the first import in every Python service entrypoint
- Never hardcode secrets — use `os.environ["VAR_NAME"]` and fail fast if missing
- Do not modify NBI field names — use exact names from PRD section 3
- All K8s resources go in namespace `infra-advisor` (except Kafka→`kafka`, Airflow→`airflow`, DD→`datadog`)
- All Deployment manifests must include `imagePullSecrets: [{name: ghcr-pull-secret}]`
- Container images are at `ghcr.io/kyletaylored/infra-advisor-ai/<service>:latest`

## Phase order
Implement phases sequentially. Check @specs/ for current phase task list.
Current progress: @claude-progress.txt
