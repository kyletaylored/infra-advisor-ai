# must be first import — monkey-patches httpx, openai, langchain at import time
import ddtrace.auto

import asyncio
import json
import logging
import os
import random
import re
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Body, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from agent import build_llm, build_mcp_client, run_agent
from conversations import (
    create_conversation,
    delete_conversation,
    get_conversation,
    init_db,
    list_conversations,
    save_messages,
)
from kafka_consumer import start_consumer_thread
from memory import append_exchange, clear_session, get_redis, get_session_model, set_session_model
from observability.llm_obs import enable_llm_obs, submit_user_feedback
from observability.tracing import current_span_id, current_trace_id

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

# ─── Global singletons initialised in lifespan ────────────────────────────────

_mcp_client = None
_llm = None
_mcp_connected = False
_llm_connected = False
_AVAILABLE_MODELS: list[str] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _mcp_client, _llm, _mcp_connected, _llm_connected, _AVAILABLE_MODELS

    # Enable LLM Observability before any LLM calls
    enable_llm_obs()

    logger.info("agent-api starting up")

    # Bootstrap conversation DB schema (no-op if DATABASE_URL unset)
    try:
        init_db()
    except Exception as exc:
        logger.warning("conversation DB init failed (non-fatal): %s", exc)

    # Build MCP client
    try:
        _mcp_client = build_mcp_client()
        # Probe connectivity by listing tools
        await _mcp_client.get_tools()
        _mcp_connected = True
        logger.info("MCP client connected")
    except Exception as exc:
        logger.warning(
            "MCP client failed to connect (will retry per-request): %s", exc)
        _mcp_connected = False

    # Build LLM
    try:
        _llm = build_llm()
        _llm_connected = True
        logger.info("LLM client initialized")
    except Exception as exc:
        logger.warning("LLM client failed to initialize: %s", exc)
        _llm_connected = False

    # Parse available model list from env
    raw_models = os.environ.get("AVAILABLE_MODELS", "gpt-4.1-mini")
    _AVAILABLE_MODELS.extend(m.strip()
                             for m in raw_models.split(",") if m.strip())
    if not _AVAILABLE_MODELS:
        _AVAILABLE_MODELS.append("gpt-4.1-mini")

    # Start Kafka consumer background thread (non-fatal if Kafka unavailable)
    if _mcp_client:
        try:
            start_consumer_thread(_mcp_client)
        except Exception as exc:
            logger.warning(
                "Kafka consumer thread failed to start (non-fatal): %s", exc)

    # Seed suggestion pool and start background top-up loop
    if _llm:
        asyncio.create_task(_pool_maintenance_loop(_llm))

    yield

    logger.info("agent-api shutting down")


app = FastAPI(
    title="InfraAdvisor Agent API",
    description="LangChain ReAct agent API for infrastructure consulting queries",
    version="0.1.0",
    lifespan=lifespan,
)


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    trace_id = current_trace_id()
    logger.exception("Unhandled exception on %s %s",
                     request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc), "trace_id": trace_id},
    )


# ─── Request / response schemas ───────────────────────────────────────────────


class QueryRequest(BaseModel):
    query: str
    session_id: str | None = None
    model: str | None = None


class QueryResponse(BaseModel):
    answer: str
    sources: list[str]
    trace_id: str | None
    span_id: str | None
    session_id: str
    model: str


class SuggestionsRequest(BaseModel):
    query: str
    answer: str
    sources: list[str] = []
    session_id: str | None = None


class SuggestionItem(BaseModel):
    label: str
    query: str


class SuggestionsResponse(BaseModel):
    suggestions: list[SuggestionItem]


# All available tools — kept here so the suggestions prompt stays in sync with main.py TOOL_NAMES
_ALL_TOOLS = (
    "get_bridge_condition (FHWA National Bridge Inventory — structural ratings, ADT, sufficiency), "
    "get_disaster_history (FEMA disaster declarations and hazard mitigation grants), "
    "get_energy_infrastructure (EIA electricity generation and capacity by state/fuel), "
    "get_water_infrastructure (EPA SDWIS water system compliance and TWDB water plans), "
    "get_ercot_energy_storage (ERCOT Texas grid energy storage resource 4-second charging data), "
    "search_txdot_open_data (TxDOT Open Data portal — AADT traffic counts, construction projects, highway datasets), "
    "search_project_knowledge (firm knowledge base — case studies, risk frameworks, templates), "
    "draft_document (generate SOW, risk summary, cost estimate, or funding memo), "
    "get_procurement_opportunities (SAM.gov and grants.gov — active federal contract opportunities and open grant programs), "
    "get_contract_awards (USASpending.gov — historical federal contract awards for competitive intelligence and pricing benchmarks), "
    "search_web_procurement (Brave Search — state and local RFPs, bond elections, and government budget announcements)"
)

_FALLBACK_SUGGESTIONS: list[SuggestionItem] = [
    SuggestionItem(label="Deficient bridges",
                   query="List structurally deficient bridges in Texas with ADT over 10,000, sorted by sufficiency rating lowest first."),
    SuggestionItem(label="SDWA violations",
                   query="Which Texas community water systems have open Safe Drinking Water Act violations serving more than 10,000 people?"),
    SuggestionItem(label="Infrastructure opportunities",
                   query="What active federal procurement opportunities exist for infrastructure engineering services on SAM.gov with NAICS codes for civil or environmental work?"),
    SuggestionItem(label="Disaster risk counties",
                   query="Which Texas counties have received 5 or more FEMA disaster declarations since 2010, and what hazard types are most frequent?"),
]

_SUGGESTIONS_PROMPT = """\
You are generating follow-up question suggestions for an AI assistant serving consultants at an \
Architecture, Engineering, Construction, Operations, and Management (AECOM) firm.

The user just asked:
{query}

The AI used these data tools: {sources}

The AI answered (truncated):
{answer}

Available tools the user can query next:
{tools}

Generate exactly 4 concise follow-up questions that are natural next steps given this conversation. \
Each should explore a different AECOM practice area angle — engineering risk, construction delivery, \
operational resilience, management/BD, or document drafting. \
Keep labels short (2-5 words, no emojis). Keep queries specific, data-grounded, and immediately actionable.

Return ONLY valid JSON, no markdown fences, no explanation:
{{"suggestions": [{{"label": "...", "query": "..."}}, {{"label": "...", "query": "..."}}, {{"label": "...", "query": "..."}}, {{"label": "...", "query": "..."}}]}}"""

# ─── Suggestion pool (Redis-backed, grows over time) ──────────────────────────

_POOL_KEY = "infra-advisor:suggestions:pool"
_POOL_MAX = 80   # max items retained in pool
_POOL_MIN = 20   # trigger async refill below this count
_POOL_REFILL_INTERVAL = 1800  # seconds between background top-ups (30 min)

# Four rotating prompts — one per AECOM practice area focus — so the pool
# accumulates diverse content across disciplines over time.
_POOL_BATCH_PROMPTS = [
    # Engineering: structural, civil, environmental
    """\
Generate exactly 10 specific opening questions an infrastructure engineer at an AECOM-style consulting \
firm would ask an AI assistant backed by FHWA NBI, EPA SDWIS, EIA, ERCOT, TxDOT, and FEMA data.
Focus on: structural condition rankings, sufficiency ratings, scour risk, water system violations, \
energy grid capacity by fuel type, traffic volume thresholds, and cross-hazard exposure.
Every question must cite a specific threshold, data field, geography, or time window. No generic questions. \
No emojis in labels. Labels 2-5 words.
Return ONLY valid JSON, no markdown:
{{"suggestions": [{{"label": "...", "query": "..."}}, ... 10 items ...]}}""",

    # Construction: procurement, delivery, project data
    """\
Generate exactly 10 specific opening questions a construction project manager or BD director at an \
AECOM-style consulting firm would ask an AI assistant backed by SAM.gov, USASpending.gov, and state \
procurement portals.
Focus on: active federal solicitations, contract award benchmarks by NAICS code, incumbent contractor \
analysis, grant program deadlines, bond election schedules, and price-per-unit benchmarks.
Every question must reference a specific NAICS code, agency, dollar threshold, or geography. No emojis in labels. Labels 2-5 words.
Return ONLY valid JSON, no markdown:
{{"suggestions": [{{"label": "...", "query": "..."}}, ... 10 items ...]}}""",

    # Operations: resilience, risk, asset lifecycle
    """\
Generate exactly 10 specific opening questions an asset manager or resilience planner at an AECOM-style \
consulting firm would ask an AI assistant backed by FEMA OpenFEMA, FHWA NBI, EPA SDWIS, and EIA data.
Focus on: repeat disaster declarations by county and hazard type, flood and scour risk to bridge assets, \
water system outage history, grid stress events, multi-hazard exposure scoring, and infrastructure age profiles.
Every question must reference a specific hazard type, county, time range, or asset class. No emojis in labels. Labels 2-5 words.
Return ONLY valid JSON, no markdown:
{{"suggestions": [{{"label": "...", "query": "..."}}, ... 10 items ...]}}""",

    # Management/Advisory: documents, BD, firm knowledge
    """\
Generate exactly 10 specific opening questions a program manager or practice leader at an AECOM-style \
consulting firm would ask an AI assistant with access to a firm knowledge base, document drafting tools, \
and procurement intelligence.
Focus on: SOW scaffolds for specific project types, risk framework selection, funding memo positioning, \
order-of-magnitude cost estimates, competitive intelligence summaries, and similar prior project retrieval.
Every question must describe a concrete deliverable, project type, or decision context. No emojis in labels. Labels 2-5 words.
Return ONLY valid JSON, no markdown:
{{"suggestions": [{{"label": "...", "query": "..."}}, ... 10 items ...]}}""",
]


def _pool_size() -> int:
    try:
        return get_redis().llen(_POOL_KEY)
    except Exception:
        return 0


def _pool_get_random(n: int) -> list[SuggestionItem]:
    """Return n random items from the pool without removing them."""
    try:
        all_raw: list[str] = get_redis().lrange(_POOL_KEY, 0, -1)
        if len(all_raw) < n:
            return []
        return [SuggestionItem(**json.loads(r)) for r in random.sample(all_raw, n)]
    except Exception:
        return []


def _pool_add(items: list[SuggestionItem]) -> None:
    """Append items to the pool and trim to _POOL_MAX."""
    if not items:
        return
    try:
        client = get_redis()
        client.rpush(_POOL_KEY, *[json.dumps({"label": i.label, "query": i.query}) for i in items])
        size = client.llen(_POOL_KEY)
        if size > _POOL_MAX:
            client.ltrim(_POOL_KEY, size - _POOL_MAX, -1)
    except Exception as exc:
        logger.warning("pool_add failed: %s", exc)


async def _fill_pool(llm: Any) -> None:
    """Generate one batch of suggestions and append to the pool."""
    prompt = random.choice(_POOL_BATCH_PROMPTS)
    try:
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        content = response.content if isinstance(response.content, str) else str(response.content)
        items = _parse_suggestions(content)
        if items:
            _pool_add(items)
            logger.info("suggestion pool refilled: +%d items (pool=%d)", len(items), _pool_size())
    except Exception as exc:
        logger.warning("_fill_pool failed: %s", exc)


async def _pool_maintenance_loop(llm: Any) -> None:
    """Background task: seed pool on startup, then top it up every 30 minutes."""
    if _pool_size() < 4:
        await _fill_pool(llm)
    while True:
        await asyncio.sleep(_POOL_REFILL_INTERVAL)
        try:
            if _pool_size() < _POOL_MIN:
                await _fill_pool(llm)
        except Exception as exc:
            logger.warning("pool maintenance iteration failed: %s", exc)


def _parse_suggestions(text: str) -> list[SuggestionItem]:
    """Extract and validate suggestion JSON from LLM response text."""
    try:
        data = json.loads(text.strip())
        items = data.get("suggestions", [])
        return [SuggestionItem(label=s["label"], query=s["query"]) for s in items[:4] if "label" in s and "query" in s]
    except Exception:
        pass

    # Fallback: extract first JSON object from response
    match = re.search(r'\{.*"suggestions".*\}', text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group())
            items = data.get("suggestions", [])
            return [SuggestionItem(label=s["label"], query=s["query"]) for s in items[:4] if "label" in s and "query" in s]
        except Exception:
            pass

    return []


# ─── Endpoints ────────────────────────────────────────────────────────────────


@app.get("/models")
async def list_models() -> dict:
    """Return the list of available Azure OpenAI deployment names."""
    return {"models": _AVAILABLE_MODELS, "default": _AVAILABLE_MODELS[0] if _AVAILABLE_MODELS else "gpt-4.1-mini"}


@app.post("/query", response_model=QueryResponse)
async def query(
    body: QueryRequest,
    x_session_id: str | None = Header(default=None, alias="X-Session-ID"),
    x_dd_rum_session_id: str | None = Header(default=None, alias="X-DD-RUM-Session-ID"),
    x_conversation_id: str | None = Header(default=None, alias="X-Conversation-ID"),
    x_user_id: str | None = Header(default=None, alias="X-User-ID"),
) -> QueryResponse:
    """Run the InfraAdvisor agent against a user query."""
    session_id = x_session_id or body.session_id or str(uuid.uuid4())

    if not _mcp_client or not _llm:
        raise HTTPException(
            status_code=503,
            detail="Agent not ready — MCP or LLM client unavailable",
        )

    # Resolve deployment: request body > session memory > default
    if body.model and body.model in _AVAILABLE_MODELS:
        deployment = body.model
    else:
        deployment = get_session_model(session_id)
        if deployment not in _AVAILABLE_MODELS and _AVAILABLE_MODELS:
            deployment = _AVAILABLE_MODELS[0]

    result = await run_agent(
        query=body.query,
        session_id=session_id,
        mcp_client=_mcp_client,
        deployment=deployment,
        rum_session_id=x_dd_rum_session_id,
    )

    append_exchange(session_id, body.query, result["answer"])
    set_session_model(session_id, deployment)

    trace_id = current_trace_id()
    span_id = current_span_id()

    # Persist exchange to conversation DB (non-blocking, non-fatal)
    if x_conversation_id and x_user_id:
        try:
            save_messages(
                conv_id=x_conversation_id,
                user_query=body.query,
                ai_answer=result["answer"],
                sources=result["tools_called"],
                trace_id=trace_id,
                span_id=span_id,
            )
        except Exception as exc:
            logger.warning("save_messages failed (non-fatal): %s", exc)

    return QueryResponse(
        answer=result["answer"],
        sources=result["tools_called"],
        trace_id=trace_id,
        span_id=span_id,
        session_id=session_id,
        model=deployment,
    )


@app.post("/suggestions", response_model=SuggestionsResponse)
async def suggestions(body: SuggestionsRequest) -> SuggestionsResponse:
    """Generate 4 LLM-powered follow-up question suggestions for the given conversation turn."""
    if not _llm:
        return SuggestionsResponse(suggestions=_FALLBACK_SUGGESTIONS)

    sources_str = ", ".join(
        body.sources) if body.sources else "general knowledge"
    prompt = _SUGGESTIONS_PROMPT.format(
        query=body.query[:500],
        sources=sources_str,
        answer=body.answer[:800],
        tools=_ALL_TOOLS,
    )

    try:
        response = await _llm.ainvoke([HumanMessage(content=prompt)])
        content = response.content if isinstance(
            response.content, str) else str(response.content)
        parsed = _parse_suggestions(content)
        if parsed:
            return SuggestionsResponse(suggestions=parsed)
    except Exception as exc:
        logger.warning("Suggestions LLM call failed: %s", exc)

    return SuggestionsResponse(suggestions=_FALLBACK_SUGGESTIONS)


@app.get("/suggestions/initial", response_model=SuggestionsResponse)
async def initial_suggestions() -> SuggestionsResponse:
    """Return 4 random suggestions from the Redis pool; fall back to LLM if pool is empty."""
    picked = _pool_get_random(4)
    if picked:
        # Async top-up if pool is running low — doesn't block the response
        if _pool_size() < _POOL_MIN and _llm:
            asyncio.create_task(_fill_pool(_llm))
        return SuggestionsResponse(suggestions=picked)

    # Pool empty (first boot or Redis unavailable) — call LLM directly and seed pool
    if not _llm:
        return SuggestionsResponse(suggestions=_FALLBACK_SUGGESTIONS)
    try:
        prompt = random.choice(_POOL_BATCH_PROMPTS)
        response = await _llm.ainvoke([HumanMessage(content=prompt)])
        content = response.content if isinstance(response.content, str) else str(response.content)
        parsed = _parse_suggestions(content)
        if parsed:
            _pool_add(parsed)
            return SuggestionsResponse(suggestions=parsed[:4])
    except Exception as exc:
        logger.warning("initial_suggestions fallback LLM call failed: %s", exc)
    return SuggestionsResponse(suggestions=_FALLBACK_SUGGESTIONS)


@app.get("/tools")
async def list_tools() -> list[dict]:
    """List all available MCP tools with their name, description, and parameter schema."""
    if not _mcp_client:
        raise HTTPException(status_code=503, detail="MCP client not available")
    tools = await _mcp_client.get_tools()
    result = []
    for t in tools:
        schema: dict = {}
        try:
            if hasattr(t, "args_schema") and t.args_schema is not None:
                schema = t.args_schema.model_json_schema()
        except Exception:
            pass
        result.append(
            {"name": t.name, "description": t.description, "parameters": schema})
    return result


@app.post("/tools/{tool_name}")
async def invoke_tool(
    tool_name: str,
    params: dict[str, Any] = Body(default={}),
) -> dict:
    """Directly invoke an MCP tool by name with the given parameters."""
    if not _mcp_client:
        raise HTTPException(status_code=503, detail="MCP client not available")
    tools = await _mcp_client.get_tools()
    tool = next((t for t in tools if t.name == tool_name), None)
    if not tool:
        raise HTTPException(
            status_code=404, detail=f"Tool '{tool_name}' not found")

    start = time.monotonic()
    try:
        result = await tool.ainvoke(params)
    except Exception as exc:
        return {
            "tool_name": tool_name,
            "error": str(exc),
            "duration_ms": round((time.monotonic() - start) * 1000, 2),
        }

    return {
        "tool_name": tool_name,
        "result": result,
        "duration_ms": round((time.monotonic() - start) * 1000, 2),
    }


_VALID_RATINGS = {"positive", "negative", "reported"}


class FeedbackRequest(BaseModel):
    trace_id: str
    span_id: str
    rating: str  # "positive" | "negative" | "reported"
    session_id: str | None = None


@app.post("/feedback", status_code=204)
async def feedback(body: FeedbackRequest) -> None:
    """Record user feedback for an agent response in Datadog LLM Observability."""
    if body.rating not in _VALID_RATINGS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid rating '{body.rating}'. Must be one of: {sorted(_VALID_RATINGS)}",
        )
    submit_user_feedback(
        trace_id=body.trace_id,
        span_id=body.span_id,
        rating=body.rating,
        session_id=body.session_id,
    )


# ─── Conversation endpoints ───────────────────────────────────────────────────


class ConversationCreateRequest(BaseModel):
    title: str = "New Conversation"
    model: str | None = None
    backend: str = "python"


@app.post("/conversations", status_code=201)
async def create_conv(
    body: ConversationCreateRequest,
    x_user_id: str | None = Header(default=None, alias="X-User-ID"),
) -> dict:
    """Create a new conversation record for the given user."""
    if not x_user_id:
        raise HTTPException(status_code=401, detail="X-User-ID header required")
    result = create_conversation(
        user_id=x_user_id,
        title=body.title,
        model=body.model,
        backend=body.backend,
    )
    if result is None:
        raise HTTPException(status_code=503, detail="Conversation storage unavailable")
    return result


@app.get("/conversations")
async def list_convs(
    x_user_id: str | None = Header(default=None, alias="X-User-ID"),
) -> dict:
    """List all conversations for the authenticated user."""
    if not x_user_id:
        raise HTTPException(status_code=401, detail="X-User-ID header required")
    return {"conversations": list_conversations(x_user_id)}


@app.get("/conversations/{conversation_id}")
async def get_conv(
    conversation_id: str,
    x_user_id: str | None = Header(default=None, alias="X-User-ID"),
) -> dict:
    """Return a conversation and all its messages."""
    if not x_user_id:
        raise HTTPException(status_code=401, detail="X-User-ID header required")
    result = get_conversation(conversation_id, x_user_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return result


@app.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conv(
    conversation_id: str,
    x_user_id: str | None = Header(default=None, alias="X-User-ID"),
) -> None:
    """Delete a conversation and all its messages."""
    if not x_user_id:
        raise HTTPException(status_code=401, detail="X-User-ID header required")
    deleted = delete_conversation(conversation_id, x_user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")


@app.get("/health")
async def health() -> dict:
    """Liveness probe — returns service status and connectivity."""
    return {
        "status": "ok",
        "service": os.environ.get("DD_SERVICE", "infraadvisor-agent-api"),
        "mcp_connected": _mcp_connected,
        "llm_connected": _llm_connected,
    }


@app.delete("/session/{session_id}")
async def delete_session(session_id: str) -> dict:
    """Clear Redis session memory for the given session ID."""
    deleted = clear_session(session_id)
    return {"session_id": session_id, "cleared": deleted}
