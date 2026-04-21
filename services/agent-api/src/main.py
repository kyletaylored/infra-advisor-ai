import ddtrace.auto  # must be first import — monkey-patches httpx, openai, langchain at import time

import json
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Body, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from agent import build_llm, build_mcp_client, run_agent
from kafka_consumer import start_consumer_thread
from memory import append_exchange, clear_session, get_session_model, set_session_model
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

    # Build MCP client
    try:
        _mcp_client = build_mcp_client()
        # Probe connectivity by listing tools
        await _mcp_client.get_tools()
        _mcp_connected = True
        logger.info("MCP client connected")
    except Exception as exc:
        logger.warning("MCP client failed to connect (will retry per-request): %s", exc)
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
    _AVAILABLE_MODELS.extend(m.strip() for m in raw_models.split(",") if m.strip())
    if not _AVAILABLE_MODELS:
        _AVAILABLE_MODELS.append("gpt-4.1-mini")

    # Start Kafka consumer background thread (non-fatal if Kafka unavailable)
    if _mcp_client and _llm:
        try:
            start_consumer_thread(_mcp_client, _llm)
        except Exception as exc:
            logger.warning("Kafka consumer thread failed to start (non-fatal): %s", exc)

    yield

    logger.info("agent-api shutting down")


app = FastAPI(
    title="InfraAdvisor Agent API",
    description="LangChain ReAct agent API for infrastructure consulting queries",
    version="0.1.0",
    lifespan=lifespan,
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
    "draft_document (generate SOW, risk summary, cost estimate, or funding memo)"
)

_FALLBACK_SUGGESTIONS: list[SuggestionItem] = [
    SuggestionItem(label="🌉 Deficient Texas bridges", query="Pull all structurally deficient bridges in Texas with ADT over 10,000 and last inspection before 2022."),
    SuggestionItem(label="⚡ ERCOT storage trends", query="What are current energy storage resource charging patterns in the ERCOT Texas grid?"),
    SuggestionItem(label="💧 SDWA violations", query="Which Texas community water systems have open Safe Drinking Water Act violations serving more than 10,000 people?"),
    SuggestionItem(label="🌊 Recent FEMA declarations", query="What major disaster declarations have occurred in Texas in the last 3 years?"),
]

_SUGGESTIONS_PROMPT = """\
You are generating follow-up question suggestions for an infrastructure consulting AI assistant.

The user just asked:
{query}

The AI used these data tools: {sources}

The AI answered (truncated):
{answer}

Available tools the user can query next:
{tools}

Generate exactly 4 concise follow-up questions that are natural next steps given this conversation. \
Each should explore a different angle — risk, cost, comparison, or document drafting. \
Keep labels short (2-5 words with a relevant emoji). Keep queries specific and actionable.

Return ONLY valid JSON, no markdown fences, no explanation:
{{"suggestions": [{{"label": "...", "query": "..."}}, {{"label": "...", "query": "..."}}, {{"label": "...", "query": "..."}}, {{"label": "...", "query": "..."}}]}}"""


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
    )

    append_exchange(session_id, body.query, result["answer"])
    set_session_model(session_id, deployment)

    return QueryResponse(
        answer=result["answer"],
        sources=result["tools_called"],
        trace_id=current_trace_id(),
        span_id=current_span_id(),
        session_id=session_id,
        model=deployment,
    )


@app.post("/suggestions", response_model=SuggestionsResponse)
async def suggestions(body: SuggestionsRequest) -> SuggestionsResponse:
    """Generate 4 LLM-powered follow-up question suggestions for the given conversation turn."""
    if not _llm:
        return SuggestionsResponse(suggestions=_FALLBACK_SUGGESTIONS)

    sources_str = ", ".join(body.sources) if body.sources else "general knowledge"
    prompt = _SUGGESTIONS_PROMPT.format(
        query=body.query[:500],
        sources=sources_str,
        answer=body.answer[:800],
        tools=_ALL_TOOLS,
    )

    try:
        response = await _llm.ainvoke([HumanMessage(content=prompt)])
        content = response.content if isinstance(response.content, str) else str(response.content)
        parsed = _parse_suggestions(content)
        if parsed:
            return SuggestionsResponse(suggestions=parsed)
    except Exception as exc:
        logger.warning("Suggestions LLM call failed: %s", exc)

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
        result.append({"name": t.name, "description": t.description, "parameters": schema})
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
        raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found")

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
