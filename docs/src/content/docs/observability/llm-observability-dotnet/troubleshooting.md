---
title: Troubleshooting
description: The bugs we hit getting to a clean pure-OTel pipeline, with fixes.
sidebar:
  order: 7
  label: Troubleshooting
---

These are the issues we actually hit — not hypothetical ones. If you're seeing a symptom that isn't here, the [Recipes → Debug why an evaluator isn't running](../recipes/#debug-why-an-evaluator-isnt-running) flow is a good starting point.

## `source:undefined` on spans

Resource attribute missing. Fix: `TelemetrySetup.cs` adds `["source"] = "otel"` to the resource. Required because DD's external-evals API expects `source:otel` on the eval payload — keep both layers consistent.

## Trace tree split across two trace IDs (dual-tracer)

Our biggest historical bug. Symptom: `invoke_agent` lived on one trace_id while `classify_domain` / `retrieve_best_practices` / `embeddings` lived on a different one — same logical request, two halves in the UI.

**Cause:** the Datadog .NET tracer was admission-injected (`admission.datadoghq.com/dotnet-lib.version: v3`) and ran in parallel with the OTel SDK. DD tracer extracted `x-datadog-trace-id` from RUM's request headers; OTel SDK ignored that header and generated its own W3C trace_id. They never agreed.

**Fix:** removed the admission annotation entirely. OTel SDK reads RUM's W3C `traceparent` natively — single trace_id end-to-end. What we lose without the DD tracer: Code Origin for Spans, ASM/AAP RASP, IAST. For a pure-OTel LLM demo, that's the right trade.

## Postgres host shows as a pod IP in DBM UI

Without `reported_hostname` in the autodiscovery check config, the Postgres host renders as the pod's ephemeral IP. Set `"reported_hostname": "infra-advisor-postgres"` in the `ad.datadoghq.com/postgres.checks` annotation — DBM UI shows the stable name.

## MCP server missing from trace tree

The MCP server emitted zero trace spans because its `TelemetrySetup` only configured `.WithMetrics(...)` — no `.WithTracing` block. Fix:
- Bump `ModelContextProtocol.AspNetCore` to 1.3+ (first stable; ships the `Experimental.ModelContextProtocol` ActivitySource + traceparent propagation through MCP message metadata).
- Add `.WithTracing(...)` with `AddAspNetCoreInstrumentation`, `AddHttpClientInstrumentation`, `AddSource("Experimental.ModelContextProtocol")`.
- On the client side, `AddSource("Experimental.ModelContextProtocol")` too — captures the CLIENT-side MCP spans that bridge `execute_tool` to the server's request span.

## MCP tool calls return HTTP 400 / 404 across replicas

MCP 1.3.0's HTTP transport is session-stateful by default — each request after `initialize` carries an `Mcp-Session-Id` bound to one server pod. When the K8s Service round-robins to a different replica, follow-up requests fail. Two options:
- **`sessionAffinity: ClientIP`** on the Service (what we ship) — pins each client to one backend for the session lifetime.
- **`Stateless=true`** on `WithHttpTransport(options => …)` — server-side. Caveat: the .NET MCP **client** library still issues session-aware requests, so a stateless server returns 400 on every tool call. Use affinity instead.

## Evals not appearing on traces

`EVAL_SAMPLE_RATE` defaults to 0.1 — only 10 % of queries are scored. To verify the path works, temporarily set it to 1.0 in the configmap, redeploy, run one query, look for eval rows on that trace. Also confirm `DD_API_KEY` is set; without it `DatadogEvalsClient` silently no-ops. The admin UI's diagnostics panel will show submissions with `success=false + "DD_API_KEY not set"` in that case — fastest way to spot the misconfiguration.

## Image pull stuck after restart

`ImagePullBackOff` on a new replica usually means the `ghcr-pull-secret` Token has expired. Generate a fresh GitHub PAT with `read:packages`, `make create-ghcr-secret`, then `kubectl rollout restart`.

## References

- [DD OTel instrumentation setup](https://docs.datadoghq.com/llm_observability/setup/sdk/opentelemetry)
- [ModelContextProtocol C# SDK](https://github.com/modelcontextprotocol/csharp-sdk)
