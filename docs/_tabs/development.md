---
title: Development
icon: fas fa-code
order: 7
permalink: /development/
---

Everything needed to run InfraAdvisor AI locally, write and run tests, and follow the project's coding standards.

## Repository layout

```
infra-advisor-ai/
  .github/workflows/       CI (ci.yml, build-push.yml, docs.yml)
  datadog/
    dashboards/            5 dashboard JSON files
    monitors/              3 monitor JSON files
    synthetics/            1 browser test
    datadog-agent.yaml     DatadogAgent custom resource
  docs/                    This documentation site
  infra/bicep/             Azure Bicep IaC modules + parameters
  k8s/                     Kubernetes manifests by service/system
    airflow/               Helm values.yaml
    agent-api/             Deployment, Service, HPA, ConfigMap
    agent-api-dotnet/      Deployment, Service, HPA, ConfigMap (.NET)
    auth-api/              Deployment, Service, ConfigMap
    kafka/                 Strimzi KafkaCluster + KafkaTopics
    mailhog/               Deployment, Service
    mcp-server/            Deployment, Service, ConfigMap
    mcp-server-dotnet/     Deployment, Service, ConfigMap (.NET)
    postgres/              StatefulSet, Service, PVC
    redis/                 Deployment, Service
    secrets/               Secret templates (not committed with values)
    ui/                    Deployment, Service, Ingress
  services/
    agent-api/             Python FastAPI + LangChain
    agent-api-dotnet/      ASP.NET Core 10 + OpenTelemetry
    auth-api/              Python FastAPI + SQLAlchemy
    ingestion/             Airflow DAGs + helpers
    load-generator/        Python CronJob
    mcp-server/            Python FastMCP
    mcp-server-dotnet/     .NET ModelContextProtocol.AspNetCore
    ui/                    React TypeScript
  specs/                   Product Requirements Document
  Makefile                 All deployment and build automation
  .env.example             Environment variable template
```

## Python service layout (uniform across all 4 services)

```
services/<name>/
  src/
    main.py              Entrypoint (import ddtrace.auto is line 1)
    <modules>.py
    observability/
      metrics.py         Custom Datadog metric helpers
      tracing.py         Span ID / trace ID helpers
      llm_obs.py         LLM Obs helpers (agent-api only)
  tests/
    test_<module>.py
  pyproject.toml         uv project config, dependencies, ruff settings
  Dockerfile
```

## Key constraints

These constraints are enforced by CI and code review. Do not work around them.

| Constraint | Why |
|-----------|-----|
| `import ddtrace.auto` must be the first import in every service entrypoint | ddtrace must patch libraries before they are imported; any earlier import bypasses instrumentation |
| All K8s Deployments and CronJobs must include `imagePullSecrets: [{name: ghcr-pull-secret}]` | GHCR is private; pods without this secret will fail to pull images |
| Never hardcode secrets — use `os.environ["VAR_NAME"]` | Secrets live in K8s Secrets, not in code |
| Do not rename NBI field names | The FHWA schema uses exact uppercase names; renaming breaks the MCP tool's field mapping |
| No top-level third-party imports in Airflow DAG files | The dag-processor runs with a minimal Python environment; top-level imports of pandas/openai/etc. crash it |
| `schedule=` not `schedule_interval=` in DAG constructors | Airflow 3.x removed `schedule_interval` |

## Sections in this chapter

- [Local Setup](local-setup) — Running all services locally without AKS
- [Testing](testing) — Test coverage, running tests, mock patterns, CI behavior
- [Conventions](conventions) — Python, K8s, Datadog, Airflow, and Git conventions
- [Project Map](../agent-guides/project-map) — Complete service + namespace + dependency reference
- [Core Conventions (Agent Guide)](../agent-guides/core-conventions) — Detailed coding patterns for AI agent contributors
- [Build, Test, Verify (Agent Guide)](../agent-guides/build-test-verify) — Command-by-command build and verification reference
