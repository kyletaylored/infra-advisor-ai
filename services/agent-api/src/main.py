import ddtrace.auto  # must be first import — monkey-patches httpx, openai, langchain at import time
from ddtrace import patch_all

patch_all()

import logging
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from agent import build_llm, build_mcp_client, run_agent
from kafka_consumer import start_consumer_thread
from memory import append_exchange, clear_session
from observability.llm_obs import enable_llm_obs
from observability.tracing import current_trace_id

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _mcp_client, _llm, _mcp_connected, _llm_connected

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


class QueryResponse(BaseModel):
    answer: str
    sources: list[str]
    trace_id: str | None
    session_id: str


# ─── Endpoints ────────────────────────────────────────────────────────────────


@app.post("/query", response_model=QueryResponse)
async def query(
    body: QueryRequest,
    x_session_id: str | None = Header(default=None, alias="X-Session-ID"),
) -> QueryResponse:
    """Run the InfraAdvisor agent against a user query."""
    # Resolve session ID: header > body > generate new
    session_id = x_session_id or body.session_id or str(uuid.uuid4())

    if not _mcp_client or not _llm:
        raise HTTPException(
            status_code=503,
            detail="Agent not ready — MCP or LLM client unavailable",
        )

    result = await run_agent(
        query=body.query,
        session_id=session_id,
        mcp_client=_mcp_client,
        llm=_llm,
    )

    # Persist exchange to Redis session memory
    append_exchange(session_id, body.query, result["answer"])

    return QueryResponse(
        answer=result["answer"],
        sources=result["sources"],
        trace_id=current_trace_id(),
        session_id=session_id,
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
