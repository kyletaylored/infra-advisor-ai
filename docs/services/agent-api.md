---
title: Agent API (Python)
parent: Services
nav_order: 2
---

# Agent API (Python)

**Port:** 8001 | **Framework:** FastAPI + LangChain ReAct + LangGraph | **Replicas:** 2

The Agent API is the reasoning core of InfraAdvisor AI. A parallel .NET implementation is documented at [Agent API (.NET)](agent-api-dotnet). It receives natural-language queries, routes them to the appropriate specialist agent, executes MCP tool calls, synthesizes answers, and maintains session memory in Redis.

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

### `POST /conversations`

Create a new conversation record. Returns a conversation object with a server-generated UUID that the client should send as `X-Conversation-ID` on subsequent `/query` calls.

**Headers:** `X-User-ID: <user-id>` (required)

**Request body (all optional):**
```json
{ "title": "Bridge analysis session", "model": "gpt-4.1-mini", "backend": "python" }
```

**Response:**
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "user_id": "alice@example.com",
  "title": "Bridge analysis session",
  "model": "gpt-4.1-mini",
  "backend": "python",
  "created_at": "2026-05-05T10:00:00Z",
  "updated_at": "2026-05-05T10:00:00Z",
  "message_count": 0
}
```

Returns `503` when `DATABASE_URL` is not configured (persistence disabled).

---

### `GET /conversations`

List all conversations for a user, sorted by `updated_at` descending.

**Headers:** `X-User-ID: <user-id>` (required)

**Response:** Array of conversation summary objects (same shape as above, plus `message_count`).

---

### `GET /conversations/{id}`

Fetch a single conversation with its full message history.

**Headers:** `X-User-ID: <user-id>` (required)

**Response:** Conversation summary plus a `messages` array:
```json
{
  "id": "...",
  "messages": [
    {
      "id": "...",
      "role": "user",
      "content": "What bridges in Harris County...",
      "sources": [],
      "created_at": "2026-05-05T10:00:05Z"
    },
    {
      "id": "...",
      "role": "assistant",
      "content": "I found 18 bridges...",
      "sources": ["get_bridge_condition"],
      "trace_id": "3421959702764693",
      "span_id": "8721043291846321",
      "created_at": "2026-05-05T10:00:08Z"
    }
  ]
}
```

---

### `DELETE /conversations/{id}`

Delete a conversation and all its messages. Returns `204 No Content` on success, `404` if not found or not owned by the requesting user.

**Headers:** `X-User-ID: <user-id>` (required)

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

## Conversation persistence

When `DATABASE_URL` is set, the Agent API stores every user/assistant exchange in PostgreSQL. This enables the conversation history sidebar in the UI.

**Schema:**

```sql
conversations (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     TEXT NOT NULL,
    title       TEXT NOT NULL DEFAULT 'New Conversation',
    model       TEXT,
    backend     TEXT DEFAULT 'python',   -- 'python' | 'dotnet'
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
)

messages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,       -- 'user' | 'assistant'
    content         TEXT NOT NULL,
    sources         JSONB NOT NULL DEFAULT '[]',
    trace_id        TEXT,               -- ddtrace trace ID for APM linking
    span_id         TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
```

Tables are created on startup via `init_db()` (idempotent `CREATE TABLE IF NOT EXISTS`). If `DATABASE_URL` is unset the service starts normally — all conversation endpoints return `[]` or `503`.

**Enabling persistence:**

```bash
# Set DATABASE_URL in your .env before running make create-agent-api-secret
DATABASE_URL=postgresql://user:pass@postgres.infra-advisor.svc.cluster.local:5432/infraadvisor
make create-agent-api-secret
```

**How the UI wires it in:** The UI creates a conversation on the first message of a new session, then sends `X-Conversation-ID` and `X-User-ID` on every subsequent `/query` call. Each exchange is saved automatically.

**DD_DBM_PROPAGATION_MODE:** Set to `full` in `k8s/agent-api/configmap.yaml`. Every PostgreSQL query issued by this service includes a SQL comment with the full ddtrace trace context, enabling **"View Trace"** links from Datadog Database Monitoring query samples back to the originating APM trace.

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
