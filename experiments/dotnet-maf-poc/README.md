# .NET MAF POC

Microsoft Agents Framework (`Microsoft.Agents.AI` 1.5.0) variant of the
M.E.AI-only POC. Same external behavior — same `/chat` endpoint, same
HTML chat UI, same tools, same collector wiring — but adds the agent
abstraction layer that produces `invoke_agent` spans, sessions that
populate `gen_ai.conversation.id`, and the `AIContextProvider` hook for
custom memory components.

## What this POC validates

Four things the M.E.AI-only POC couldn't prove:

| Validation point | How |
|---|---|
| 1. **`invoke_agent` span** (the "agent" kind in DD LLMObs) | Stack two `.UseOpenTelemetry()` decorators — one on the chat client, one on the agent builder — and verify the resulting trace has `invoke_agent → chat → execute_tool` |
| 2. **`gen_ai.conversation.id` for multi-turn grouping** | Map browser `?c=<id>` → `AgentSession` via in-memory `SessionStore`. Same session reused across turns by conversation ID; MAF stamps `conversation.id` on every span. |
| 3. **`AIContextProvider` hook API** | Attach a minimal `MemoryProvider` via `ChatClientAgentOptions.AIContextProviders`. Provider prints to stdout on both `ProvideAIContextAsync` (pre-call) and `StoreAIContextAsync` (post-call) hooks to prove they fire. |
| 4. **Real MCP tools** (replaces mock tools) | `ModelContextProtocol.Client`'s `HttpClientTransport` + `McpClient.CreateAsync` connects at startup to a port-forwarded `mcp-server-dotnet`. `mcpClient.ListToolsAsync()` returns `AITool`s that spread directly into the agent's `Tools = [.. mcpTools]`. M.E.AI's `.UseFunctionInvocation()` then emits an `execute_tool` span for each MCP call the model decides to make. |

## Span hierarchy expected

```
HTTP server span                       ← AspNetCore instrumentation (root)
└── invoke_agent <agent-name>          ← MAF .UseOpenTelemetry() on AGENT
    └── chat <model>                   ← M.E.AI .UseOpenTelemetry() on CHAT CLIENT
        ├── POST <azure-openai-url>    ← HttpClient instrumentation
        └── execute_tool <name>        ← M.E.AI .UseFunctionInvocation()
            └── chat <model> (follow-up turn after tool result)
```

`gen_ai.conversation.id` should be set on the `invoke_agent` span (and
inherited / set on the children) for every span in the same session.

## The full agent pipeline in one place

```csharp
// 1. IChatClient pipeline (M.E.AI layer): chat + execute_tool spans
builder.Services.AddSingleton<IChatClient>(sp =>
    sp.GetRequiredService<AzureOpenAIClient>()
        .GetChatClient(deployment)
        .AsIChatClient()
        .AsBuilder()
        .UseFunctionInvocation()
        .UseOpenTelemetry(configure: cfg => cfg.EnableSensitiveData = true)
        .Build());

// 2. AIAgent pipeline (MAF layer): invoke_agent span
builder.Services.AddSingleton<AIAgent>(sp =>
    new ChatClientAgent(sp.GetRequiredService<IChatClient>(), new ChatClientAgentOptions
    {
        Name = "infra-advisor-poc-agent",
        ChatOptions = new ChatOptions
        {
            Instructions = "...system prompt...",
            Tools        = tools,
        },
        AIContextProviders = [sp.GetRequiredService<MemoryProvider>()],
    })
    .AsBuilder()
    .UseOpenTelemetry(configure: cfg => cfg.EnableSensitiveData = true)
    .Build());

// 3. TracerProvider: register both ActivitySources
builder.Services.AddOpenTelemetry()
    .ConfigureResource(r => r.AddService(serviceName))
    .WithTracing(t => t
        .AddAspNetCoreInstrumentation()
        .AddHttpClientInstrumentation()
        .AddSource("Experimental.Microsoft.Extensions.AI")  // chat + execute_tool
        .AddSource("Microsoft.Agents.AI")                   // invoke_agent
        .AddOtlpExporter(o => { o.Endpoint = ...; o.Protocol = OtlpExportProtocol.HttpProtobuf; }));
```

## Running

Three terminals (or use `&` / tmux):

```bash
# Terminal 1 — port-forward mcp-server-dotnet so the POC can reach it
kubectl port-forward -n infra-advisor svc/mcp-server-dotnet 8000:8000

# Terminal 2 — POC + collector
make otel-maf-poc                  # http://localhost:5007

# Terminal 3 (optional) — watch the spans the collector receives
make logs-otel-collector
```

When the POC starts you should see:

```
[mcp] connecting to http://localhost:8000/mcp
[mcp] connected; loaded 11 tool(s): get_bridge_condition, get_disaster_history, ...
```

If MCP isn't reachable (port-forward not running) the POC fails fast at
startup with an error pointing to the port-forward command. To override
the MCP URL (e.g., point at a locally-Docker-running MCP server) set
**`MAF_POC_MCP_URL`** in your shell — not `MCP_SERVER_URL`, since that
name is already in the root `.env` for the production agent-api service
(pointing at the cluster-internal URL) and gets force-overridden by the
Makefile target to the port-forwarded `localhost:8000` value.

The local OTel Collector + its `transform/llmobs` processor (which
injects `ml_app`) is shared with the M.E.AI-only POC — no separate
collector needed.

## Open API questions to resolve while running

The Microsoft Agents Framework samples on `main` use API shapes that
differ slightly from 1.5.0 stable. Things we couldn't confirm
statically and need to probe live with the running POC:

- **`AgentSession.Id`** — the sample reads it but the property doesn't
  exist on the 1.5.0 type. There's clearly *something* identifying a
  session (the framework uses it for `gen_ai.conversation.id` injection);
  we just don't have a public accessor name. Run the POC and check the
  span attributes for the actual conversation-id value.
- **`InvokingContext.RequestMessages`** — same situation; the property
  is referenced in the sample's memory provider but isn't on the 1.5.0
  surface for `InvokingContext`. May be on `InvokedContext` only, or
  renamed. The `MemoryProvider` currently logs the context type's full
  name on each invocation to surface this.
- **Source name for the `invoke_agent` span** — registered as
  `Microsoft.Agents.AI` based on the namespace; will verify via
  `make logs-otel-collector` debug exporter output.

## Migration relationship

This POC isn't a destination — it's a feasibility check for migrating
`services/agent-api-dotnet/` off the hand-rolled
`LlmTelemetry`/`AgentService` design and onto MAF agents. The Q1/Q2/Q3
design decisions from the parent POC's README still apply (single agent
vs per-specialist, router as agent vs code dispatcher, session
persistence pattern) — this POC just confirms the building blocks work.

Once the three validation points above are confirmed in DD LLMObs, the
production migration becomes the next step.
