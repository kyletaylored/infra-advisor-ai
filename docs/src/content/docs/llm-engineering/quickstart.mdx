---
title: Quickstart
description: From "nothing instrumented" to "/query showing up in LLM Observability" — the minimum viable loop, in both Python and .NET.
sidebar:
  order: 2
---

import { Tabs, TabItem, Aside } from '@astrojs/starlight/components';

By the end of this page, one HTTP request will produce a complete trace in **DD → LLM Observability → Traces**: workflow → agent → llm → tool, with the prompt, model, token counts, and cost attached. That's the minimum viable loop. Everything else in this guide builds on it.

## Prerequisites

- A Datadog tenant with LLM Observability enabled.
- `DD_API_KEY` available as a Kubernetes secret (or via env in dev).
- A DD Agent running on-cluster (we use the Helm chart `datadog/datadog`).
- An OpenAI-compatible LLM endpoint — we use Azure OpenAI with a `gpt-4.1-mini` deployment.

## Step 1 — Configure the SDK

<Tabs syncKey="lang">
  <TabItem label="Python (ddtrace)">

**Install:** `uv add ddtrace` (pulls in `ddtrace>=3.11` with LangChain / OpenAI / MCP integrations).

**Bootstrap as the first import** so ddtrace can patch downstream libraries before they load:

```python
# services/agent-api/src/main.py
import ddtrace.auto  # MUST be the first import
from observability.llm_obs import enable_llm_obs

# ... later, inside FastAPI lifespan startup:
enable_llm_obs()
```

`enable_llm_obs()` wraps the LLMObs SDK's `enable()` call so a missing config doesn't take the service down:

```python
# services/agent-api/src/observability/llm_obs.py
from ddtrace.llmobs import LLMObs

def enable_llm_obs() -> None:
    ml_app = os.environ.get("DD_LLMOBS_ML_APP", "infra-advisor-ai")
    agentless = os.environ.get("DD_LLMOBS_AGENTLESS_ENABLED", "false").lower() == "true"
    LLMObs.enable(ml_app=ml_app, agentless_enabled=agentless)
```

  </TabItem>
  <TabItem label=".NET (OpenTelemetry)">

**Install:** add to your `.csproj`:

```xml
<PackageReference Include="OpenTelemetry.Extensions.Hosting" Version="1.10.0" />
<PackageReference Include="OpenTelemetry.Instrumentation.AspNetCore" Version="1.10.1" />
<PackageReference Include="OpenTelemetry.Instrumentation.Http" Version="1.10.0" />
<PackageReference Include="OpenTelemetry.Exporter.OpenTelemetryProtocol" Version="1.10.0" />
```

**Bootstrap in `Program.cs`** before `builder.Build()`:

```csharp
// services/agent-api-dotnet/Program.cs
TelemetrySetup.Configure(builder);
```

`TelemetrySetup.Configure` wires `AddOpenTelemetry().WithTracing(...).WithMetrics(...)` and the OTLP exporter. The `ml_app` attribute is **not** set in app code — it's mirrored from `service.name` by a `transform/llmobs` processor in the in-cluster Datadog Agent's OTel collector. Keeps app code pure-OTel.

  </TabItem>
</Tabs>

## Step 2 — Set the env vars

Both services read config from the pod environment. Set in `helm/values.yaml`:

<Tabs syncKey="lang">
  <TabItem label="Python (ddtrace)">

```yaml
agentApi:
  env:
    DD_LLMOBS_ENABLED: "true"
    DD_LLMOBS_ML_APP: "infra-advisor-ai"
    DD_LLMOBS_AGENTLESS_ENABLED: "false"  # export via on-cluster Agent
    DD_SERVICE: "infra-advisor-agent-api"
    DD_ENV: "production"
    DD_VERSION: "1.0.0"
    DD_AGENT_HOST: "datadog-agent.datadog.svc.cluster.local"
    DD_TRACE_AGENT_PORT: "8126"
```

`DD_LLMOBS_AGENTLESS_ENABLED=false` means traces go through the DD Agent on the same cluster (cheaper, observable from `kubectl logs`). Flip to `true` only if you don't have an Agent.

  </TabItem>
  <TabItem label=".NET (OpenTelemetry)">

```yaml
agentApiDotnet:
  env:
    OTEL_SERVICE_NAME: "infra-advisor-agent-api-dotnet"
    OTEL_EXPORTER_OTLP_ENDPOINT: "http://datadog-agent.datadog.svc.cluster.local:4318"
    OTEL_EXPORTER_OTLP_PROTOCOL: "http/protobuf"
    DD_ENV: "production"
    DD_VERSION: "1.0.0"
```

The DD Agent must run the OTel collector ports open (`4318` for HTTP/protobuf OTLP). The collector handles `ml_app` injection and DD-LLMObs classification on the OTLP ingest side.

  </TabItem>
</Tabs>

## Step 3 — Deploy and fire a query

```bash
make deploy-k8s
kubectl rollout restart deploy/agent-api -n infra-advisor       # Python
kubectl rollout restart deploy/agent-api-dotnet -n infra-advisor  # .NET

# Hit the endpoint
curl -sS https://your-app/api/query -H 'Content-Type: application/json' \
  -d '{"query":"What bridges in Texas are in poor condition?"}' | jq -r '.answer'
```

## Step 4 — Find it in Datadog

1. **DD → LLM Observability → Traces.**
2. Filter `@ml_app:infra-advisor-ai` (or `infra-advisor-agent-api-dotnet`).
3. Click the most recent trace. You should see a span tree with `workflow`, `agent`, `llm`, and `tool` nodes.

If the trace doesn't appear within 1–2 minutes, jump to the [troubleshooting checklist on the Instrumentation page for your language](./instrumentation/python/#troubleshooting).

## What you've built

A complete LLMObs trace per request. From here, the rest of the guide is about getting **more signal** out of that pipeline:

- [Instrumentation](./instrumentation/python/) — what's auto-captured vs what you explicitly annotate.
- [Monitoring → Spans and traces](./monitoring/spans-and-traces/) — querying, sessions, RUM linking, agent view.
- [Evaluations → External](./evaluations/external/) — attach quality scores to each trace.

<Aside type="tip">
**Don't skip the eval step.** A pipeline that traces successfully but never gets a quality signal attached will eventually drift in production without your detection. [Set up at least one managed evaluation](./evaluations/managed/) before you call it shipped.
</Aside>
