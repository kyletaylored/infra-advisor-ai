"""Integration tests for the agent-api.

Strategy
--------
* The MCP client is mocked — no live MCP server required.
* AzureChatOpenAI is patched to return a canned ReAct-format response.
* Redis is patched — no live Redis required.
* All tests verify the HTTP contract (POST /query, GET /health, DELETE /session).

Coverage
--------
* POST /query returns answer, sources, trace_id, session_id
* GET /health returns mcp_connected and llm_connected booleans
* DELETE /session/{session_id} returns cleared: true
* Session ID generated when not supplied
* Session ID from X-Session-ID header is preserved in response
* 503 returned when agent not ready
"""

import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Env setup
# ---------------------------------------------------------------------------

os.environ.setdefault("DD_AGENT_HOST", "localhost")
os.environ.setdefault("DD_TRACE_ENABLED", "false")
os.environ.setdefault("DD_LLMOBS_ENABLED", "false")
os.environ.setdefault("AZURE_OPENAI_ENDPOINT", "https://mock.openai.azure.com")
os.environ.setdefault("AZURE_OPENAI_API_KEY", "mock-key")
os.environ.setdefault("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini")
os.environ.setdefault("MCP_SERVER_URL", "http://mock-mcp:8000/mcp")
os.environ.setdefault("REDIS_HOST", "localhost")
# Shared with auth-api in prod; any non-empty value works for unit tests.
os.environ.setdefault("JWT_SECRET", "test-secret-for-agent-api-unit-tests")

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


# ---------------------------------------------------------------------------
# Test JWT — all protected endpoints require Authorization: Bearer <token>
# ---------------------------------------------------------------------------

def _make_test_token() -> str:
    """Mint a JWT signed with the test JWT_SECRET so protected endpoints accept it."""
    from datetime import datetime, timedelta, timezone

    from jose import jwt
    return jwt.encode(
        {
            "sub": "test-user-id",
            "email": "tester@datadoghq.com",
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        },
        os.environ["JWT_SECRET"],
        algorithm="HS256",
    )


_TEST_AUTH_HEADER = {"Authorization": f"Bearer {_make_test_token()}"}


# ---------------------------------------------------------------------------
# Fake LangChain tool
# ---------------------------------------------------------------------------


class _FakeTool:
    name = "get_bridge_condition"
    description = "Query FHWA NBI"

    async def ainvoke(self, _):
        return [{"structure_number": "1100200000B0042", "_source": "FHWA NBI"}]


# ---------------------------------------------------------------------------
# Canned agent result
# ---------------------------------------------------------------------------

CANNED_RESULT = {
    "answer": "Bridge 1100200000B0042 has a sufficiency rating of 42.7.",
    "sources": ["FHWA NBI"],
    "tools_called": ["get_bridge_condition"],
    "query_domain": "transportation",
}

# Canned run_agent_stream() — an async generator, not a coroutine, so it's
# patched in directly (new=_canned_stream) rather than via AsyncMock.
CANNED_STREAM_EVENTS = [
    {"event": "step", "step": "classify_domain", "status": "done", "detail": "transportation"},
    {"event": "step", "step": "route_query", "status": "done", "detail": "engineering"},
    {"event": "tool_call_start", "id": "1", "name": "get_bridge_condition", "args_json": "{}"},
    {
        "event": "tool_call_end",
        "id": "1",
        "name": "get_bridge_condition",
        "status": "ok",
        "result_summary": "1 records",
        "sources": ["FHWA NBI"],
        "duration_ms": 12.3,
    },
    {"event": "text_chunk", "chunk": "Bridge 1100200000B0042 has a sufficiency rating of 42.7."},
    {
        "event": "done",
        "session_id": "test-session",
        "model": "gpt-4.1-mini",
        "sources": ["FHWA NBI"],
        "tools_called": ["get_bridge_condition"],
        "query_domain": "transportation",
    },
]


async def _canned_stream(*args, **kwargs):
    for evt in CANNED_STREAM_EVENTS:
        yield evt


def _parse_sse_blocks(body: str) -> list[tuple[str, dict]]:
    """Split a raw SSE response body into (event_name, parsed_data) pairs."""
    parsed = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        lines = block.splitlines()
        event_name = next(l for l in lines if l.startswith("event:"))[len("event:"):].strip()
        data_line = next(l for l in lines if l.startswith("data:"))[len("data:"):].strip()
        parsed.append((event_name, json.loads(data_line)))
    return parsed


# ---------------------------------------------------------------------------
# App fixture with all external I/O patched
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    """TestClient with MCP + LLM + Redis all mocked."""
    mock_mcp = MagicMock()
    mock_mcp.get_tools = AsyncMock(return_value=[_FakeTool()])

    mock_llm = MagicMock()

    with (
        patch("main.build_mcp_client", return_value=mock_mcp),
        patch("main.build_llm", return_value=mock_llm),
        patch("main.run_agent", new=AsyncMock(return_value=CANNED_RESULT)),
        patch("main.append_exchange"),
        patch("main.enable_llm_obs"),
        patch("main.start_consumer_thread"),
        # Lifespan unconditionally spawns a background suggestion-pool
        # maintenance loop whenever _llm is truthy (main.py ~105). Left
        # unpatched it makes real blocking Redis calls to localhost:6379 —
        # nothing is listening in the test env, and since those redis-py
        # calls are synchronous, the resulting retry/backoff loop monopolizes
        # the single event loop TestClient/anyio shares across tests. Every
        # test tolerated this by accident (no other test needed the loop to
        # stay responsive after fixture setup) until the /query/stream tests
        # arrived, which must keep pumping the loop to drain the SSE
        # generator — patch it out the same way start_consumer_thread is.
        patch("main._pool_maintenance_loop", new=AsyncMock(return_value=None)),
        patch("main._mcp_connected", True, create=True),
        patch("main._llm_connected", True, create=True),
    ):
        from main import app

        # Force lifespan to set up globals by calling with real TestClient.
        # headers= sets default headers on every request — including the
        # Authorization: Bearer <jwt> that the protected endpoints now require.
        with TestClient(
            app,
            raise_server_exceptions=True,
            headers=_TEST_AUTH_HEADER,
        ) as c:
            # Manually inject the mocks since lifespan ran before patches in some envs
            import main as _main

            _main._mcp_client = mock_mcp
            _main._llm = mock_llm
            _main._mcp_connected = True
            _main._llm_connected = True
            yield c


# ---------------------------------------------------------------------------
# Tests — POST /query
# ---------------------------------------------------------------------------


def test_query_returns_expected_fields(client):
    resp = client.post("/query", json={"query": "Tell me about Texas bridges."})
    assert resp.status_code == 200
    body = resp.json()
    assert "answer" in body
    assert "sources" in body
    assert "session_id" in body
    assert isinstance(body["sources"], list)


def test_query_generates_session_id_when_absent(client):
    resp = client.post("/query", json={"query": "Test query."})
    assert resp.status_code == 200
    session_id = resp.json()["session_id"]
    assert session_id  # non-empty
    # Should look like a UUID
    import uuid

    uuid.UUID(session_id)  # raises if invalid


def test_query_preserves_session_id_from_header(client):
    my_session = "test-session-12345"
    resp = client.post(
        "/query",
        json={"query": "Bridge query."},
        headers={"X-Session-ID": my_session},
    )
    assert resp.status_code == 200
    assert resp.json()["session_id"] == my_session


def test_query_preserves_session_id_from_body(client):
    my_session = "body-session-99999"
    resp = client.post(
        "/query",
        json={"query": "Bridge query.", "session_id": my_session},
    )
    assert resp.status_code == 200
    assert resp.json()["session_id"] == my_session


def test_query_answer_is_non_empty(client):
    resp = client.post("/query", json={"query": "What are the top bridges in Texas?"})
    assert resp.status_code == 200
    assert resp.json()["answer"] != ""


# ---------------------------------------------------------------------------
# Tests — POST /query/stream
# ---------------------------------------------------------------------------


def test_query_stream_returns_event_stream_content_type(client):
    with patch("main.run_agent_stream", new=_canned_stream):
        resp = client.post("/query/stream", json={"query": "Tell me about Texas bridges."})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")


def test_query_stream_emits_expected_event_sequence(client):
    with patch("main.run_agent_stream", new=_canned_stream):
        resp = client.post("/query/stream", json={"query": "Tell me about Texas bridges."})

    events = _parse_sse_blocks(resp.text)
    names = [name for name, _ in events]
    assert names == ["step", "step", "tool_call_start", "tool_call_end", "text_chunk", "done"]

    by_name = dict(events)
    assert by_name["tool_call_start"]["name"] == "get_bridge_condition"
    assert by_name["tool_call_end"]["sources"] == ["FHWA NBI"]
    assert by_name["tool_call_end"]["status"] == "ok"
    assert by_name["text_chunk"]["chunk"] != ""
    assert by_name["done"]["tools_called"] == ["get_bridge_condition"]
    assert by_name["done"]["query_domain"] == "transportation"
    # trace_id/span_id are injected by the /query/stream handler itself
    # (not present in the canned run_agent_stream events) — just confirm
    # the keys exist, since DD tracing is disabled in this test env.
    assert "trace_id" in by_name["done"]
    assert "span_id" in by_name["done"]


def test_query_stream_503_when_mcp_not_ready():
    """POST /query/stream must return a plain 503, not a broken stream, when not ready."""
    with (
        patch("main.build_mcp_client", side_effect=Exception("MCP unavailable")),
        patch("main.build_llm", return_value=MagicMock()),
        patch("main.enable_llm_obs"),
        patch("main.start_consumer_thread"),
        patch("main._pool_maintenance_loop", new=AsyncMock(return_value=None)),
    ):
        if "main" in sys.modules:
            del sys.modules["main"]
        from main import app

        with TestClient(
            app,
            raise_server_exceptions=False,
            headers=_TEST_AUTH_HEADER,
        ) as c:
            import main as _main

            _main._mcp_client = None
            _main._llm = None
            resp = c.post("/query/stream", json={"query": "test"})
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Tests — GET /health
# ---------------------------------------------------------------------------


def test_health_returns_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "mcp_connected" in body
    assert "llm_connected" in body


def test_health_mcp_connected_is_bool(client):
    resp = client.get("/health")
    assert isinstance(resp.json()["mcp_connected"], bool)


# ---------------------------------------------------------------------------
# Tests — DELETE /session/{session_id}
# ---------------------------------------------------------------------------


def test_delete_session_returns_cleared(client):
    with patch("main.clear_session", return_value=True):
        resp = client.delete("/session/test-session-abc")
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == "test-session-abc"
    assert body["cleared"] is True


def test_delete_session_cleared_false_when_not_found(client):
    with patch("main.clear_session", return_value=False):
        resp = client.delete("/session/nonexistent")
    assert resp.status_code == 200
    assert resp.json()["cleared"] is False


# ---------------------------------------------------------------------------
# Tests — 503 when agent not ready
# ---------------------------------------------------------------------------


def test_query_503_when_mcp_not_ready():
    """POST /query must return 503 when _mcp_client is None."""
    with (
        patch("main.build_mcp_client", side_effect=Exception("MCP unavailable")),
        patch("main.build_llm", return_value=MagicMock()),
        patch("main.enable_llm_obs"),
        patch("main.start_consumer_thread"),
    ):
        # Re-import app fresh so lifespan runs with patches in effect
        if "main" in sys.modules:
            del sys.modules["main"]
        from main import app

        with TestClient(
            app,
            raise_server_exceptions=False,
            headers=_TEST_AUTH_HEADER,
        ) as c:
            import main as _main

            _main._mcp_client = None
            _main._llm = None
            resp = c.post("/query", json={"query": "test"})
        assert resp.status_code == 503
