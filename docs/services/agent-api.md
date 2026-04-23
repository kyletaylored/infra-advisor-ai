---
title: Agent API
parent: Services
nav_order: 2
---

# Agent API

**Port:** 8001 | **Framework:** FastAPI + LangChain ReAct + LangGraph | **Replicas:** 2

The Agent API is the reasoning core of InfraAdvisor AI. It receives natural-language queries, routes them to the appropriate specialist agent, executes MCP tool calls, synthesizes answers, and maintains session memory in Redis.

Every query produces a rich Datadog LLM Observability trace with a multi-level span hierarchy: workflow → router → planner → specialist → tool calls → faithfulness eval.

## Multi-agent architecture

Queries flow through three sequential agents before the final answer is assembled:

```
POST /query
  │
  ├── Router Agent (gpt-4.1-mini)
  │     Classifies domain: engineering | water_energy | business_development | document | general
  │     Cost: ~200 prompt tokens + 50 completion tokens
  │
  ├── Planner Agent (gpt-4.1-mini)
  │     Produces 1–2 sentence execution strategy
  │     Injected as context hint to the specialist
  │
  └── Specialist Agent (LangGraph ReAct executor)
        Receives curated tool subset for its domain
        Runs ReAct loop until answer is complete
        Tools vary by specialist (see below)
```

### Specialist agents and their tool subsets

| Specialist | Domain Keywords | Tools |
|------------|----------------|-------|
| `engineering` | bridge, structural, transportation, highway, road, TxDOT, construction, civil | `get_bridge_condition`, `get_disaster_history`, `get_energy_infrastructure`, `get_water_infrastructure`, `get_ercot_energy_storage`, `search_txdot_open_data`, `search_project_knowledge`, `draft_document` |
| `water_energy` | water, energy, utility, power, grid, reservoir, drought, ERCOT, EIA, EPA | `get_water_infrastructure`, `get_energy_infrastructure`, `get_ercot_energy_storage`, `search_project_knowledge`, `draft_document` |
| `business_development` | RFP, contract, award, procurement, grant, bid, opportunity, SAM.gov, funding | `get_procurement_opportunities`, `get_contract_awards`, `search_web_procurement`, `search_project_knowledge`, `draft_document` |
| `document` | draft, template, SOW, statement of work, risk, cost estimate, funding memo | `draft_document`, `search_project_knowledge` |
| `general` | (fallback) | All 11 tools |

## API endpoints

### `POST /query`

Run the multi-agent pipeline on a user query.

**Request:**
```json
{
  "query": "What bridges in Harris County have a sufficiency rating below 50?",
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "model": "gpt-4.1-mini"
}
```

**Headers:**
- `Authorization: Bearer <jwt>` — Required
- `X-Session-ID: <uuid>` — Session for Redis memory lookup
- `X-DD-RUM-Session-ID: <rum_session>` — Optional; links LLM Obs traces to RUM session replay

**Response:**
```json
{
  "answer": "I found 18 bridges in Harris County...",
  "sources": [{"tool": "get_bridge_condition", "snippet": "Structure 4803..."}],
  "trace_id": "3421959702764693",
  "span_id": "8721043291846321",
  "session_id": "550e8400-...",
  "model": "gpt-4.1-mini"
}
```

---

### `GET /suggestions/initial`

Returns 4 AECOM-focused opening suggestions from the Redis pool. No LLM call — responds in ~1ms from a pre-generated pool of up to 80 suggestions.

If the pool drops below 20 items, a background `_fill_pool()` task runs asynchronously to replenish it.

**Response:**
```json
{
  "suggestions": [
    "Which Texas bridges are rated structurally deficient and carry more than 5,000 vehicles daily?",
    "Compare federal infrastructure grant funding available for water utilities in drought-prone states.",
    "What construction contracts over $1M were awarded in Arizona for highway projects last year?",
    "Draft a risk summary for a bridge rehabilitation project in a flood-prone county."
  ]
}
```

---

### `POST /suggestions`

Generate follow-up suggestions based on conversation context. LLM-powered (gpt-4.1-mini).

**Request:**
```json
{
  "query": "Tell me about Texas bridges",
  "answer": "I found 18 bridges...",
  "domain": "engineering",
  "session_id": "..."
}
```

**Response:**
```json
{
  "suggestions": ["...", "...", "...", "..."]
}
```

---

### `GET /models`

List available Azure OpenAI deployment names.

**Response:**
```json
{
  "models": ["gpt-4.1-mini", "gpt-4.1"],
  "default": "gpt-4.1-mini"
}
```

---

### `POST /feedback`

Record user feedback for a specific LLM Observability trace.

**Request:**
```json
{
  "trace_id": "3421959702764693",
  "span_id": "8721043291846321",
  "rating": "positive",
  "session_id": "..."
}
```

Valid ratings: `positive`, `negative`, `reported`

**Response:** 204 No Content

The feedback is submitted via `LLMObs.submit_evaluation()` and appears under the **Evaluations** tab on the LLM Obs trace in Datadog.

---

### `GET /health`

Returns service readiness status and connectivity to MCP Server and Redis.

---

### `POST /tools/{tool_name}`

Directly invoke an MCP tool (debug/sandbox endpoint). Used by the UI Sandbox tab.

---

## Session memory

Session history is stored in Redis with a 24-hour TTL:

```
Key: infra-advisor:session:{session_id}:memory
Value: JSON list of {"type": "human"|"ai", "content": "..."} exchange objects
TTL: 86400 seconds (refreshed on write)
```

The model preference is persisted separately:
```
Key: infra-advisor:session:{session_id}:model
Value: "gpt-4.1-mini" | "gpt-4.1"
TTL: 86400 seconds
```

On `DELETE /session/{session_id}`, both keys are removed.

## Error responses

On unhandled 500 errors, the API returns:
```json
{
  "detail": "Error description",
  "trace_id": "3421959702764693"
}
```

The `trace_id` is the ddtrace trace ID for the current request. The UI renders a "View trace →" link that opens the Datadog APM trace directly.

## Observability

**LLM Observability span tree** (per query):

```
workflow: query-processing
  task: load-history              (tags: history.turns)
  agent: router                   (tags: query.domain)
    chat_model (auto-instrumented)
  agent: planner
    chat_model (auto-instrumented)
  agent: infra-advisor            (tags: tools_called.count, sources.count)
    tool: get_bridge_condition    (auto-instrumented via langchain-mcp-adapters)
      http (auto-instrumented, outbound to mcp-server)
    chat_model (auto-instrumented, ReAct reasoning)
  task: extract-sources           (tags: sources.count)

(async, separate trace)
task: faithfulness-eval           (tags: session.id, eval.faithfulness_score)
  llm (auto-instrumented OpenAI call)
```

**Session linking:**
- `session.id` — set to RUM session ID when `X-DD-RUM-Session-ID` header is present; falls back to internal chat UUID
- `session.chat_id` — always the Redis session key UUID
- Enables "View session replay" link from LLM Obs trace detail panel

## Kafka integration

**Producer (eval results):** After each query, the agent publishes to `infra.eval.results`:
```json
{
  "query_id": "...",
  "session_id": "...",
  "answer": "...",
  "tools_called": ["get_bridge_condition"],
  "faithfulness_score": 0.92,
  "latency_ms": 3241,
  "model": "gpt-4.1-mini"
}
```

**Consumer (load generator):** A background thread consumes `infra.query.events` published by the Load Generator CronJob. Each message runs through the full `run_agent()` pipeline, producing real LLM Obs traces for synthetic queries.
