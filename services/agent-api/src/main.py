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

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_core.messages import HumanMessage
from pydantic import BaseModel
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from agent import build_llm, build_mcp_client, run_agent, run_agent_stream
from auth import limiter, require_auth
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
    # dd.trace_id/dd.span_id placeholders are what actually make
    # DD_LOGS_INJECTION=true correlate a log line to its trace — ddtrace
    # patches LogRecord with these attributes regardless, but they never
    # reach the rendered output (and so are invisible to Datadog's log
    # pipeline) unless the format string references them explicitly.
    format=(
        "%(asctime)s %(levelname)s [%(name)s] "
        "[dd.service=%(dd.service)s dd.env=%(dd.env)s dd.version=%(dd.version)s "
        "dd.trace_id=%(dd.trace_id)s dd.span_id=%(dd.span_id)s] - %(message)s"
    ),
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

# Rate limiter — must be attached to app.state before SlowAPIMiddleware
# is added. Keyed by user ID (preferred) or client IP, per auth.py._rate_key.
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)


@app.exception_handler(RateLimitExceeded)
async def _ratelimit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"detail": f"Rate limit exceeded: {exc.detail}"},
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


# Tool catalog — keep IN SYNC with the MCP server tool descriptions and with
# .NET's SuggestionService.ToolCatalog (services/agent-api-dotnet/Services/
# SuggestionService.cs). Suggestion-pool prompts inject this string so the
# LLM generates questions that map cleanly to a single tool call with
# realistic args, instead of questions that no tool can actually answer.
_TOOL_CATALOG = """\
Available MCP tools and what they can answer (use this list to ground every \
suggestion in something the system can actually look up):

1. get_bridge_condition — FHWA National Bridge Inventory. All US bridges over 20 ft. Fields: condition ratings (0-9), BRIDGE_CONDITION Good/Fair/Poor, scour-critical flag, ADT, year built, location. Input: 2-char FIPS state code (e.g. '48' = TX, '06' = CA). Works nationwide.
2. get_disaster_history — OpenFEMA major-disaster + emergency declarations 1953-present. Nationwide. Filter by states (2-letter abbrev), incident_types (Flood, Hurricane, Tornado, etc.), date range.
3. get_energy_infrastructure — EIA state-level annual electricity statistics. All 50 states. data_series: 'generation' | 'capacity' | 'fuel_mix'. Fuel codes: SUN, WND, NG, COL, NUC, HYC, BIO, GEO, PET.
4. get_ercot_energy_storage — ERCOT public data API. TEXAS ONLY (~90% of TX, excludes El Paso and SPP regions). Battery storage 4-second charging data. Use for ERCOT-specific grid questions only.
5. get_water_infrastructure — Dispatched by query_type: 'water_systems' (EPA SDWIS, all US), 'violations' (EPA SDWIS, nationwide), 'water_plan_projects' (TWDB, TEXAS ONLY, regions A-P). Always cite PWSID for individual systems.
6. search_txdot_open_data — TxDOT Open Data portal (ArcGIS). TEXAS ONLY. AADT counts, construction projects, highway geometry. query_type: 'catalog_search' | 'traffic_counts' | 'construction_projects'.
7. search_project_knowledge — Azure AI Search index of internal firm content (case studies, prior SOWs, templates, best practices). Call BEFORE draft_document.
8. draft_document — Renders one of 4 templates: 'scope_of_work' | 'risk_summary' | 'cost_estimate_scaffold' | 'funding_positioning_memo'. Always call search_project_knowledge first.
9. get_procurement_opportunities — SAM.gov + grants.gov. ACTIVE/OPEN federal solicitations and grants. NEVER asks for a date range. AEC NAICS: 237310 (highway), 237110 (water/sewer), 237990 (heavy civil), 541330 (engineering services).
10. get_contract_awards — USASpending.gov. HISTORICAL federal contract awards. Default window: past 2 years. CALL THIS BEFORE get_procurement_opportunities for BD research — know past winners before pursuing open opportunities.
11. search_web_procurement — Azure OpenAI web_search_preview. State/local RFPs, bond elections, budget announcements on .gov / .us / DemandStar / BidNet / BonfireHub. Use ONLY for non-federal procurement.

Hard constraints:
- For Texas-specific tools (ERCOT, TxDOT, TWDB), only use Texas in the question.
- For NAICS codes, prefer ['237310', '237110', '237990', '541330'].
- For federal BD questions, suggest the get_contract_awards → get_procurement_opportunities sequence.
- For 'recent disaster' questions, name an incident_type and a state.
- For energy questions outside Texas grid, use get_energy_infrastructure (not ERCOT)."""

# Curated golden-path seed pool — hand-verified to work end-to-end against the
# current tool set. Mirrors .NET's SuggestionService.SeedPool entry-for-entry
# so both backends give the same reliable first-touch experience. Loaded into
# the Redis pool on cold start (see _pool_maintenance_loop) and used as the
# fallback when the LLM or Redis is unavailable.
_SEED_POOL: list[SuggestionItem] = [
    # Bridges — single tool, fast
    SuggestionItem(label="Worst Texas bridges",
                   query="List the 25 worst-rated bridges in Texas by lowest condition rating."),
    SuggestionItem(label="California scour bridges",
                   query="Find structurally deficient bridges in California with scour-critical flags set."),
    SuggestionItem(label="Harris County deficient",
                   query="Show structurally deficient bridges in Harris County, Texas with ADT over 10,000."),
    # Water — single tool, two datasets
    SuggestionItem(label="Texas SDWA violations",
                   query="Which Texas community water systems have open Safe Drinking Water Act violations serving more than 10,000 people?"),
    SuggestionItem(label="Texas desalination plans",
                   query="List recommended desalination projects from the TWDB 2026 State Water Plan."),
    # FEMA disasters — single tool
    SuggestionItem(label="Recent Texas hurricanes",
                   query="How many hurricane disaster declarations has Texas had in the last 10 years?"),
    # Energy — single tool
    SuggestionItem(label="Texas renewable mix",
                   query="What's the renewable energy generation share for Texas in the last 5 years?"),
    # TxDOT — single tool, Texas-only
    SuggestionItem(label="TxDOT pavement data",
                   query="Find TxDOT Open Data datasets related to pavement condition."),
    # Federal procurement — chained tools (the BD golden path)
    SuggestionItem(label="TX highway BD",
                   query="Find recent federal contract awards for highway construction in Texas under NAICS 237310, then list open opportunities matching the same NAICS."),
    SuggestionItem(label="Water engineering RFPs",
                   query="Show active federal solicitations for water engineering services (NAICS 541330 or 237110) with bid deadlines in the next 60 days."),
    # Document drafting — chained: knowledge → draft
    SuggestionItem(label="SOW for bridge rehab",
                   query="Pull templates and prior projects for bridge rehabilitation, then draft a scope_of_work for an IH-35 bridge corridor project."),
    # Cross-domain — exercises 2-3 tools
    SuggestionItem(label="Flood-risk bridge audit",
                   query="For Harris County, Texas: list structurally deficient bridges, recent flood declarations in the last 5 years, and water systems with violations."),
]

# Deterministic 4-item slice used when the LLM/Redis path fails outright —
# error-path fallback, not the cold-start pool seed (which uses all 12).
_FALLBACK_SUGGESTIONS: list[SuggestionItem] = _SEED_POOL[:4]

_SUGGESTIONS_PROMPT = """\
You are generating follow-up question suggestions for an AI assistant serving consultants at an \
AEC/O&M (Architecture, Engineering, Construction / Operations & Maintenance) infrastructure firm.

The user just asked:
{query}

The AI used these data tools: {sources}

The AI answered (truncated):
{answer}

Available tools the user can query next:
{tools}

Generate exactly 4 concise follow-up questions that are natural next steps given this conversation. \
Each should explore a different AEC/O&M practice area angle — engineering risk, construction delivery, \
operational resilience, management/BD, or document drafting. \
Keep labels short (2-5 words, no emojis). Keep queries specific, data-grounded, and immediately actionable.

Return ONLY valid JSON, no markdown fences, no explanation:
{{"suggestions": [{{"label": "...", "query": "..."}}, {{"label": "...", "query": "..."}}, {{"label": "...", "query": "..."}}, {{"label": "...", "query": "..."}}]}}"""

# ─── Suggestion pool (Redis-backed, grows over time) ──────────────────────────

_POOL_KEY = "infra-advisor:suggestions:pool"
_POOL_MAX = 80   # max items retained in pool
_POOL_MIN = 20   # trigger async refill below this count
_POOL_REFILL_INTERVAL = 1800  # seconds between background top-ups (30 min)

# Four rotating prompts — one per AEC/O&M practice area focus — so the pool
# accumulates diverse content across disciplines over time.
_POOL_BATCH_PROMPTS = [
    # Engineering: structural, civil, environmental
    """\
Generate exactly 10 specific opening questions an infrastructure engineer at an infrastructure consulting \
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
infrastructure consulting firm would ask an AI assistant backed by SAM.gov, USASpending.gov, and state \
procurement portals.
Focus on: active federal solicitations, contract award benchmarks by NAICS code, incumbent contractor \
analysis, grant program deadlines, bond election schedules, and price-per-unit benchmarks.
Every question must reference a specific NAICS code, agency, dollar threshold, or geography. No emojis in labels. Labels 2-5 words.
Return ONLY valid JSON, no markdown:
{{"suggestions": [{{"label": "...", "query": "..."}}, ... 10 items ...]}}""",

    # Operations: resilience, risk, asset lifecycle
    """\
Generate exactly 10 specific opening questions an asset manager or resilience planner at an infrastructure \
consulting firm would ask an AI assistant backed by FEMA OpenFEMA, FHWA NBI, EPA SDWIS, and EIA data.
Focus on: repeat disaster declarations by county and hazard type, flood and scour risk to bridge assets, \
water system outage history, grid stress events, multi-hazard exposure scoring, and infrastructure age profiles.
Every question must reference a specific hazard type, county, time range, or asset class. No emojis in labels. Labels 2-5 words.
Return ONLY valid JSON, no markdown:
{{"suggestions": [{{"label": "...", "query": "..."}}, ... 10 items ...]}}""",

    # Management/Advisory: documents, BD, firm knowledge
    """\
Generate exactly 10 specific opening questions a program manager or practice leader at an infrastructure \
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
        # Curated golden-path queries first, so the very first user sees
        # hand-verified suggestions rather than waiting on an LLM call —
        # same rationale as .NET's SuggestionService cold-start seeding.
        _pool_add(_SEED_POOL)
        logger.info("suggestion pool seeded with %d curated golden-path queries", len(_SEED_POOL))
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
@limiter.limit("20/minute")
async def query(
    request: Request,
    body: QueryRequest,
    x_session_id: str | None = Header(default=None, alias="X-Session-ID"),
    x_dd_rum_session_id: str | None = Header(default=None, alias="X-DD-RUM-Session-ID"),
    x_conversation_id: str | None = Header(default=None, alias="X-Conversation-ID"),
    x_user_id: str | None = Header(default=None, alias="X-User-ID"),
    _user: dict = Depends(require_auth),
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


@app.post("/query/stream")
@limiter.limit("20/minute")
async def query_stream(
    request: Request,
    body: QueryRequest,
    x_session_id: str | None = Header(default=None, alias="X-Session-ID"),
    x_dd_rum_session_id: str | None = Header(default=None, alias="X-DD-RUM-Session-ID"),
    x_conversation_id: str | None = Header(default=None, alias="X-Conversation-ID"),
    x_user_id: str | None = Header(default=None, alias="X-User-ID"),
    _user: dict = Depends(require_auth),
) -> StreamingResponse:
    """SSE streaming counterpart to /query. Same session/model resolution as
    /query; the 503 readiness guard fires before the StreamingResponse is
    constructed so it's a normal HTTP error, not a mid-stream one — same
    shape as agent-api-dotnet's /query/stream (Program.cs)."""
    session_id = x_session_id or body.session_id or str(uuid.uuid4())

    if not _mcp_client or not _llm:
        raise HTTPException(
            status_code=503,
            detail="Agent not ready — MCP or LLM client unavailable",
        )

    if body.model and body.model in _AVAILABLE_MODELS:
        deployment = body.model
    else:
        deployment = get_session_model(session_id)
        if deployment not in _AVAILABLE_MODELS and _AVAILABLE_MODELS:
            deployment = _AVAILABLE_MODELS[0]

    async def event_stream():
        answer_parts: list[str] = []
        tools_called: list[str] = []

        # Tool-call / pipeline-step reasoning, accumulated as step/
        # tool_call_start/tool_call_end events arrive so it can be persisted
        # alongside the answer — mirrors agent-api-dotnet's Program.cs
        # UpsertStep pattern so a reloaded conversation renders identical
        # chips to the ones the user saw live.
        steps: list[dict] = []
        step_index_by_id: dict[str, int] = {}

        def upsert_step(step: dict) -> None:
            step_id = step["id"]
            if step_id in step_index_by_id:
                steps[step_index_by_id[step_id]] = step
            else:
                step_index_by_id[step_id] = len(steps)
                steps.append(step)

        async for evt in run_agent_stream(
            query=body.query,
            session_id=session_id,
            mcp_client=_mcp_client,
            deployment=deployment,
            rum_session_id=x_dd_rum_session_id,
        ):
            event_name = evt["event"]

            if event_name == "text_chunk":
                answer_parts.append(evt["chunk"])
            elif event_name == "step":
                upsert_step({
                    "kind": "internal",
                    "id": f"internal:{evt['step']}",
                    "name": evt["step"],
                    "status": evt["status"],
                    "args_json": None,
                    "result_summary": None,
                    "sources": None,
                    "duration_ms": None,
                    "detail": evt.get("detail"),
                })
            elif event_name == "tool_call_start":
                upsert_step({
                    "kind": "tool",
                    "id": evt["id"],
                    "name": evt["name"],
                    "status": "running",
                    "args_json": evt.get("args_json"),
                    "result_summary": None,
                    "sources": None,
                    "duration_ms": None,
                    "detail": None,
                })
            elif event_name == "tool_call_end":
                # Preserve args_json captured at tool_call_start — the end
                # event doesn't carry args.
                prior_idx = step_index_by_id.get(evt["id"])
                prior_args_json = steps[prior_idx]["args_json"] if prior_idx is not None else None
                upsert_step({
                    "kind": "tool",
                    "id": evt["id"],
                    "name": evt["name"],
                    "status": evt["status"],
                    "args_json": prior_args_json,
                    "result_summary": evt.get("result_summary"),
                    "sources": evt.get("sources"),
                    "duration_ms": evt.get("duration_ms"),
                    "detail": None,
                })
            elif event_name == "done":
                tools_called = evt["tools_called"]
                evt = {
                    **evt,
                    "trace_id": current_trace_id(),
                    "span_id": current_span_id(),
                }
            elif event_name == "error":
                evt = {**evt, "trace_id": current_trace_id()}

            payload = json.dumps({k: v for k, v in evt.items() if k != "event"})
            yield f"event: {event_name}\ndata: {payload}\n\n"

        full_answer = "".join(answer_parts)
        append_exchange(session_id, body.query, full_answer)
        set_session_model(session_id, deployment)

        if x_conversation_id and x_user_id:
            try:
                save_messages(
                    conv_id=x_conversation_id,
                    user_query=body.query,
                    ai_answer=full_answer,
                    sources=tools_called,
                    trace_id=current_trace_id(),
                    span_id=current_span_id(),
                    steps=steps,
                )
            except Exception as exc:
                logger.warning("save_messages failed (non-fatal): %s", exc)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.post("/suggestions", response_model=SuggestionsResponse)
@limiter.limit("60/minute")
async def suggestions(
    request: Request,
    body: SuggestionsRequest,
    _user: dict = Depends(require_auth),
) -> SuggestionsResponse:
    """Generate 4 LLM-powered follow-up question suggestions for the given conversation turn."""
    if not _llm:
        return SuggestionsResponse(suggestions=_FALLBACK_SUGGESTIONS)

    sources_str = ", ".join(
        body.sources) if body.sources else "general knowledge"
    prompt = _SUGGESTIONS_PROMPT.format(
        query=body.query[:500],
        sources=sources_str,
        answer=body.answer[:800],
        tools=_TOOL_CATALOG,
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
@limiter.limit("60/minute")
async def initial_suggestions(
    request: Request,
    _user: dict = Depends(require_auth),
) -> SuggestionsResponse:
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
async def list_tools(_user: dict = Depends(require_auth)) -> list[dict]:
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
@limiter.limit("30/minute")
async def invoke_tool(
    request: Request,
    tool_name: str,
    params: dict[str, Any] = Body(default={}),
    _user: dict = Depends(require_auth),
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
            "trace_id": current_trace_id(),
            "span_id": current_span_id(),
        }

    return {
        "tool_name": tool_name,
        "result": result,
        "duration_ms": round((time.monotonic() - start) * 1000, 2),
        # Lets the Sandbox UI link straight to this call's trace in Datadog
        # APM — the actual downstream API response body (e.g. USASpending's
        # HTTP 422 detail) is logged there via log_external_api_failure
        # (services/mcp-server/src/observability/tracing.py), correlated to
        # this trace/span via DD_LOGS_INJECTION.
        "trace_id": current_trace_id(),
        "span_id": current_span_id(),
    }


_VALID_RATINGS = {"positive", "negative", "reported"}


class FeedbackRequest(BaseModel):
    trace_id: str
    span_id: str
    rating: str  # "positive" | "negative" | "reported"
    session_id: str | None = None


@app.post("/feedback", status_code=204)
async def feedback(
    body: FeedbackRequest,
    _user: dict = Depends(require_auth),
) -> None:
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
    _user: dict = Depends(require_auth),
) -> dict:
    """Create a new conversation record for the authenticated user."""
    result = create_conversation(
        user_id=_user["sub"],
        title=body.title,
        model=body.model,
        backend=body.backend,
    )
    if result is None:
        raise HTTPException(status_code=503, detail="Conversation storage unavailable")
    return result


@app.get("/conversations")
async def list_convs(_user: dict = Depends(require_auth)) -> dict:
    """List all conversations for the authenticated user."""
    return {"conversations": list_conversations(_user["sub"])}


@app.get("/conversations/{conversation_id}")
async def get_conv(
    conversation_id: str,
    _user: dict = Depends(require_auth),
) -> dict:
    """Return a conversation and all its messages."""
    result = get_conversation(conversation_id, _user["sub"])
    if result is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return result


@app.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conv(
    conversation_id: str,
    _user: dict = Depends(require_auth),
) -> None:
    """Delete a conversation and all its messages."""
    deleted = delete_conversation(conversation_id, _user["sub"])
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
async def delete_session(
    session_id: str,
    _user: dict = Depends(require_auth),
) -> dict:
    """Clear Redis session memory for the given session ID."""
    deleted = clear_session(session_id)
    return {"session_id": session_id, "cleared": deleted}
