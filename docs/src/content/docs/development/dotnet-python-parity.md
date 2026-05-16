---
title: .NET ↔ Python feature parity
description: What .NET has that Python doesn't (yet) — a checklist for catching the Python stack back up.
---

import { Aside } from '@astrojs/starlight/components';

The .NET stack (`agent-api-dotnet`, `mcp-server-dotnet`) has accumulated significantly more functionality than the Python stack (`agent-api`, `mcp-server`) since the MAF migration. This page enumerates every difference so the Python catch-up work has a single checklist.

<Aside type="note">
**Different paths to the same outcome.** Where .NET uses **Microsoft Agents Framework + Microsoft.Extensions.AI + pure OpenTelemetry**, Python uses **LangGraph + LangChain + `ddtrace.auto`**. The DD Python `ddtrace` library bundles an LLM Observability SDK (`ddtrace.llmobs.LLMObs`); the .NET tracer does not. So some .NET features (prompt tracking, external evals) were built by hand against DD's APIs; the Python equivalents can lean on the DD SDK. The parity goal is **feature coverage**, not implementation symmetry.
</Aside>

## Architecture summary

| Layer | .NET | Python |
|---|---|---|
| Web framework | ASP.NET Core minimal API | FastAPI |
| LLM client | `Microsoft.Extensions.AI` `IChatClient` | `langchain_openai.AzureChatOpenAI` |
| Agent loop | `Microsoft.Agents.AI` `ChatClientAgent` (single agent, all tools) | LangGraph `create_react_agent` (router + 5 specialists) |
| MCP client | `ModelContextProtocol.Client` 1.3.0 | `langchain_mcp_adapters.MultiServerMCPClient` |
| Tracer | Pure OpenTelemetry SDK (OTLP → collector) | `ddtrace.auto` (DD-native protocol → local socket) |
| LLM Obs path | OTel `gen_ai.*` attrs → collector `transform/llmobs` processor | `ddtrace.llmobs.LLMObs` SDK (direct) |
| Conversation memory | MAF `AgentSession` (Redis JSON) | Hand-rolled Redis list of `ConversationMessage` |
| Streaming | `IAsyncEnumerable<AgentResponseUpdate>` over SSE | LangGraph `astream` — but only `/query` non-streaming endpoint exposed |

## Feature matrix

Legend: ✅ at parity · ◐ partial · ❌ missing · 🔵 .NET-only by design

| Capability | .NET | Python | Notes |
|---|---|---|---|
| **Agent core** |
| Multi-tool agent loop | ✅ | ✅ | Different architectures (single MAF agent vs LangGraph router+specialists) |
| Conversation history persistence | ✅ MAF `AgentSession` round-trip in Redis | ✅ Custom Redis list | Different serialization formats — sessions don't cross-migrate |
| MCP tool integration | ✅ via SDK | ✅ via LangChain adapter | |
| MCP client resilient reconnect (handles mcp-server restart without restarting agent-api) | ✅ `McpClientHolder` + retry-once on session-expired | ❌ | LangChain `MultiServerMCPClient` would need a similar wrapper |
| Multi-turn agent session ID for LLM Obs grouping | ✅ via MAF | ✅ via DD SDK `session.id` | |
| **Streaming UX** |
| Server-Sent Events `/query/stream` endpoint | ✅ | ❌ | `IAsyncEnumerable<AgentResponseUpdate>` → SSE blocks |
| Live tool-call chips in UI | ✅ for `dotnet` backend | ❌ for `python` backend | Frontend toggle currently hides streaming chips when `backend=python` |
| Token-by-token answer streaming | ✅ | ❌ | LangGraph supports `astream`; would need wiring into a streaming FastAPI endpoint |
| **Observability** |
| OTel `gen_ai.*` spans (chat / agent / tool / embedding) | ✅ M.E.AI + MAF emit natively | ◐ ddtrace LangChain integration emits, but DD-native format not OTel | |
| LLM Observability span kinds (llm / agent / tool / embedding / retrieval / task) | ✅ all 7 via auto-classification + `dd.llmobs.span.kind` tags | ◐ via `ddtrace.llmobs.LLMObs` decorators | Python could add the `task` / `retrieval` kinds via `LLMObs.workflow()` |
| `source:otel` resource attribute | ✅ | ❌ | Python uses `source:apm` because spans go through DD tracer |
| Distributed tracing across services via W3C `traceparent` | ✅ | ◐ DD propagation format by default; W3C optional | |
| Log → trace correlation | ✅ Serilog `@tr`/`@sp` | ✅ ddtrace `DD_LOGS_INJECTION` injects `dd.trace_id`/`dd.span_id` | Different injection mechanisms, same end-result |
| RUM → backend trace linkage | ✅ via W3C `traceparent` | ✅ via `x-datadog-trace-id` header | RUM sends both; each side reads what its tracer prefers |
| `Npgsql.OpenTelemetry` / `pg8000` instrumentation for DB spans | ✅ `.AddNpgsql()` | ✅ `ddtrace.psycopg` auto-instrumentation | |
| DBM ↔ APM trace correlation | ✅ via OTel attribute path (`attributes/dbm` collector processor sets `span.type=sql`) | ✅ via `DD_DBM_PROPAGATION_MODE=full` SQL-comment injection | |
| **LLM Observability — quality layer** |
| External evaluations submitted to DD `/api/intake/llm-obs/v2/eval-metric` | ✅ `DatadogEvalsClient` + `IResponseEvaluator` framework with 3 evaluators | ❌ | `ddtrace.llmobs.LLMObs.submit_evaluation()` would be the Python-side equivalent |
| `IResponseEvaluator` catalog | ✅ `CitationPresentEvaluator`, `BdToolOrderingEvaluator`, `ToolRoutingAccuracyEvaluator` | ❌ | |
| Prompt Tracking via `_dd.ml_obs.prompt_tracking` JSON attribute | ✅ `ActivityListener` stamps every `chat` + `invoke_agent` span | ❌ | LangChain doesn't have an obvious hook; the ddtrace LLMObs SDK supports prompt tracking via the `prompt` arg on `LLMObs.llm()` |
| Per-trace eval sample rate via `EVAL_SAMPLE_RATE` env | ✅ | ❌ | |
| Few-shot examples in agent system prompt | ✅ 5 worked Q → tool patterns | ❌ | LangChain prompt templates can carry them — easy port |
| Tool-routing accuracy evaluator | ✅ regex rule list | ❌ | |
| **Business metrics** |
| `infra_advisor.conversation.completed` counter (per `/query`, tagged with domain) | ✅ OTel `Meter` | ❌ Python emits some via `emit_tool_call` / `emit_external_api` but not these top-level counters | |
| `infra_advisor.tool.invoked` counter (per MCP tool call, tagged with tool name + domain) | ✅ | ❌ | |
| `infra_advisor.feedback.submitted` counter (per `/feedback`, tagged with rating) | ✅ | ❌ | |
| `infra_advisor.mcp.reconnect` counter (per MCP session-expired recovery) | ✅ | ❌ | |
| **MCP tool descriptions** |
| Enriched `[Description]` attrs with When-to-use / When-NOT-to-use / FIPS state codes / NAICS examples | ✅ all 11 tools | ❌ Python tool docstrings are shorter | The Python `tools/*.py` docstrings need the same treatment |
| `ToolCatalog` constant referenced by suggestion-pool prompts | ✅ in `SuggestionService` | ❌ Python `_POOL_BATCH_PROMPTS` lists tool names without capability detail | |
| Curated golden-path `SeedPool` for cold-start suggestions | ✅ 12 hand-verified queries | ❌ | |
| Web search via Azure OpenAI `web_search_preview` (replacement for Tavily) | ✅ | ✅ | Both already migrated |
| **MCP server side** |
| `Experimental.ModelContextProtocol` ActivitySource captured via OTel | ✅ via `AddSource(...)` | ◐ `ddtrace` auto-instruments the Python MCP server's `mcp` package | |
| `sessionAffinity: ClientIP` on K8s Service | ✅ (required by MCP 1.3.0 stateful HTTP transport) | ✅ (same Service config applies; Python MCP server uses same affinity) | |
| Server-side OTel `.WithTracing(...)` block | ✅ | N/A — Python uses ddtrace | |
| **UI** |
| Streaming consumption with inline tool chips | ✅ when `backend=dotnet` | ❌ when `backend=python` | UI toggle in admin tab |
| `ToolStepChip` component | ✅ used by streaming endpoint | N/A | |
| CitationPanel | ❌ removed (tool chips carry the info) | ❌ removed (parity already achieved) | |

## To bring Python to parity — priority ordering

If you're picking up the Python catch-up, this is the order I'd tackle items in:

### Tier 1 — Quick wins, high impact

1. **Enrich MCP tool descriptions** in `services/mcp-server/src/tools/*.py`. Mirror the `[Description]` content from the .NET tool files. Drops "agent picked wrong tool" failures.
2. **Add `ToolCatalog` constant + ground suggestion prompts** in `services/agent-api/src/main.py`. Same content as `SuggestionService.ToolCatalog` in .NET.
3. **Add few-shot examples** to the LangChain prompts in `services/agent-api/src/agent.py`. Mirror the 5 examples from `AgentSystemPrompt` in `Program.cs`.
4. **Curated `SEED_POOL`** added to the Redis suggestion pool on cold start. Same 12 entries.

### Tier 2 — Observability surface

5. **External evaluations** via `ddtrace.llmobs.LLMObs.submit_evaluation()` — Python equivalent of `DatadogEvalsClient`. Port the 3 evaluators (`citation_present`, `bd_tool_ordering`, `tool_routing_accuracy`) as plain Python functions.
6. **Business metrics counters** — three counters via `datadog.dogstatsd` or `ddtrace.tracer.metrics` matching the .NET names (`infra_advisor.conversation.completed` etc.).
7. **Prompt tracking** via the `prompt` arg on `LLMObs.llm()` decorator/context-manager — same `name`/`version`/`template` JSON shape so the DD UI's Prompt Tracking compare view works across both stacks.

### Tier 3 — UX

8. **Streaming `/query/stream` endpoint** using LangGraph's `astream` → SSE event blocks. Same event types (`step`, `tool_call_start`, `tool_call_end`, `text_chunk`, `done`, `error`) so the .NET-side UI consumer works against either backend.
9. **MCP resilient reconnect** — wrap `MultiServerMCPClient` in a holder that recreates on session-expired errors. Lower priority because Python's MCP adapter has different lifecycle semantics; may not be a problem in practice.

### Tier 4 — Bigger lifts

10. **Consider migrating from LangGraph router/specialist → single-agent pattern.** The .NET migration revealed that modern models handle the full 11-tool catalog cleanly without router-level partitioning. Net: ~500 lines deleted, fewer LLM hops, lower latency. Requires careful migration of the conversation-memory format though.

## Implementation references

For each capability, the canonical .NET implementation to port from:

| Feature | .NET file | Pattern to mirror |
|---|---|---|
| Tool descriptions | `services/mcp-server-dotnet/Tools/*.cs` `[Description(...)]` attrs | One block per tool: WHAT / scope / WHEN / WHEN NOT / args |
| Tool catalog | `services/agent-api-dotnet/Services/SuggestionService.cs` `ToolCatalog` | Single multi-line string |
| Seed pool | same file, `SeedPool` | List of `(label, query)` pairs |
| Few-shot examples | `services/agent-api-dotnet/Program.cs` `AgentSystemPrompt` | Appended to system message |
| External evals client | `services/agent-api-dotnet/Services/DatadogEvalsClient.cs` | POST to `/api/intake/llm-obs/v2/eval-metric` |
| Evaluator framework | `services/agent-api-dotnet/Services/Evaluators/IResponseEvaluator.cs` + 3 implementations | One class per evaluator, deterministic logic |
| Routing-accuracy evaluator | `services/agent-api-dotnet/Services/Evaluators/ToolRoutingAccuracyEvaluator.cs` | Regex → tool-name rule list |
| Business metrics | `services/agent-api-dotnet/Services/AgentService.cs` counter creation + emission | OTel `Meter.CreateCounter<long>` |
| SSE streaming endpoint | `services/agent-api-dotnet/Program.cs` `/query/stream` handler | Async iterator → SSE blocks |
| Stream events shape | `services/agent-api-dotnet/Models/StreamEvent.cs` | Discriminated union |
| Tool-call streaming dispatch | `services/agent-api-dotnet/Services/AgentService.cs` `RunAgentStreamingAsync` | `IAsyncEnumerable<StreamEvent>` |
| MCP client holder | `services/agent-api-dotnet/Services/McpClientHolder.cs` | Lazy connect + `RefreshAsync` + generation counter |

---

<Aside type="tip">
**Keep this page in sync.** When you ship a Python catch-up, flip the relevant row from ❌ to ✅ here as part of the same PR. When you ship a new .NET feature, add a new row marked ❌ for Python so the gap stays visible.
</Aside>
