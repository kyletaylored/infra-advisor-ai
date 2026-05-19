---
title: MCP clients in LLM Observability
description: How MCP tool calls show up in the trace tree, how W3C traceparent propagates between services, and the K8s session-affinity gotcha.
sidebar:
  order: 3
---

import { Tabs, TabItem, Aside } from '@astrojs/starlight/components';

MCP (Model Context Protocol) is how InfraAdvisor's agent talks to its tools — `mcp-server-dotnet` exposes 11 tools over HTTP, and both agent backends call them as MCP clients. From DD's perspective, MCP calls show up as `tool`-kind LLMObs spans with child HTTP spans, all under the same `trace_id` as the originating `/query`.

## What MCP looks like in a trace

```
└── execute_tool / call_tool                  tool   ← MCP client (in agent-api)
    └── HTTP POST → mcp-server                http   ← outbound HTTP
        └── [mcp-server-dotnet]
            POST /mcp/                        workflow ← AspNetCore on the server
            └── tools/call <name>             (mcp)    ← MCP server-side span
                └── HTTP → upstream API       (http)   ← FHWA / EIA / EPA / etc.
```

The keystone is W3C `traceparent` flowing through MCP's request metadata. As long as both ends register `Experimental.ModelContextProtocol` as a tracing source, propagation is automatic.

## Client-side instrumentation

<Tabs syncKey="lang">
  <TabItem label="Python (ddtrace)">

ddtrace's MCP integration ships in `ddtrace>=3.11`. Once `ddtrace.auto` is in place, `ClientSession.call_tool()` emits a `tool`-kind LLMObs span without any code change. The MCP client library (`mcp` package) is what gets patched.

LangChain's MCP adapter (`langchain-mcp-adapters`) wraps `call_tool` in a `BaseTool` that ddtrace's LangChain integration ALSO instruments, so you get **two** spans nested by default — the LangChain tool span (outer) and the MCP call span (inner). That's by design; the outer captures the LangChain-level metadata (tool name as the agent saw it), the inner captures the MCP protocol details.

  </TabItem>
  <TabItem label=".NET (OpenTelemetry)">

In .NET, the MCP C# SDK (≥1.3.0) emits OTel spans on the `Experimental.ModelContextProtocol` source. Register it on both client and server `TelemetrySetup.cs`:

```csharp
// Client (services/agent-api-dotnet/Observability/TelemetrySetup.cs)
.WithTracing(t => t
    .AddSource("Experimental.ModelContextProtocol")
    .AddSource("Experimental.Microsoft.Extensions.AI")  // emits execute_tool wrapper
    ...)

// Server (services/mcp-server-dotnet/Observability/TelemetrySetup.cs)
.WithTracing(t => t
    .AddAspNetCoreInstrumentation()
    .AddSource("Experimental.ModelContextProtocol")
    ...)
```

`Experimental.Microsoft.Extensions.AI` is what emits the outer `execute_tool` span; `Experimental.ModelContextProtocol` emits the inner protocol-level spans. Both need to be registered or your trace tree will have a hole.

  </TabItem>
</Tabs>

## Server-side instrumentation

The MCP server emits a workflow span for `POST /mcp/` (from AspNetCore auto-instrumentation) and a child MCP span per tool call. The MCP C# SDK on the server side reads incoming `traceparent` from message metadata and attaches the request to the parent trace.

If the MCP server is missing from your trace tree entirely, the cause is almost always one of:

1. **MCP SDK older than 1.3.0** — traceparent propagation didn't exist in earlier versions. Bump the package.
2. **Server `TelemetrySetup` only configured `.WithMetrics(...)`** — no `.WithTracing(...)` block. We hit this once; nothing emits without it.
3. **Source name not registered server-side** — `.AddSource("Experimental.ModelContextProtocol")` is required on the server too, not just the client.

## Session affinity (the gotcha)

MCP 1.3.0+'s HTTP transport is **session-stateful by default**. Each request after the initial `initialize` carries an `Mcp-Session-Id` header bound to one server pod. When K8s' Service round-robins a follow-up tool call to a different replica, the server returns 400 or 404 because it doesn't know about the session.

**Fix:** `sessionAffinity: ClientIP` on the MCP server's K8s Service. Pins each client to one backend for the session lifetime:

```yaml
# k8s/mcp-server-dotnet/service.yaml
apiVersion: v1
kind: Service
metadata:
  name: mcp-server-dotnet
spec:
  selector:
    app: mcp-server-dotnet
  sessionAffinity: ClientIP
  sessionAffinityConfig:
    clientIP:
      timeoutSeconds: 3600
  ports:
    - port: 8001
      targetPort: 8001
```

**Alternative:** set `Stateless = true` on `WithHttpTransport(options => ...)` server-side. But the .NET MCP **client** library still issues session-aware requests, so a stateless server returns 400 on every tool call. Use affinity instead.

<Aside type="caution">
**Don't enable replica-count autoscaling on the MCP server without affinity.** New replicas accept new sessions but break in-flight ones bound to a scaled-down pod. With affinity, in-flight sessions survive until their pod terminates gracefully.
</Aside>

## What's next

- [Spans and traces](../spans-and-traces/) — querying / filtering for tool-related traces.
- [APM correlation](../apm-correlation/) — log + DBM linkage for tool calls that hit a DB.
