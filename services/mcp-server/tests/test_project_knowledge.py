"""Tests for the search_project_knowledge tool.

Strategy
--------
* Azure AI Search calls are mocked via unittest.mock — no real search index.
* Azure OpenAI embedding calls are mocked by patching _embed_query directly.
* DD metrics are verified by patching observability.metrics.statsd.
* All env vars are set to mock values before any imports.

Coverage
--------
* search returns >= 1 chunk with content, source, domain, score fields
* document_types filter applied to search OData filter string
* domains filter applied to search OData filter string
* DD metrics emitted: rag.retrieval.top_score, rag.retrieval.chunks_returned,
  rag.index.last_updated, mcp.tool.calls, mcp.tool.latency_ms
* Embedding failure returns structured error dict
* Azure AI Search failure returns structured error dict
* top_k respected (passed as 'top' to SearchClient.search)
* Missing credentials return structured error dict
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Environment — must be set before any ddtrace imports
# ---------------------------------------------------------------------------
os.environ.setdefault("DD_AGENT_HOST", "localhost")
os.environ.setdefault("DD_TRACE_ENABLED", "false")
os.environ.setdefault("DD_DOGSTATSD_PORT", "8125")
os.environ.setdefault("AZURE_SEARCH_ENDPOINT", "https://mock.search.windows.net")
os.environ.setdefault("AZURE_SEARCH_API_KEY", "mock-key")
os.environ.setdefault("AZURE_SEARCH_INDEX_NAME", "infra-advisor-knowledge")
os.environ.setdefault("AZURE_OPENAI_ENDPOINT", "https://mock.openai.azure.com")
os.environ.setdefault("AZURE_OPENAI_API_KEY", "mock-key")
os.environ.setdefault("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-ada-002")

# ---------------------------------------------------------------------------
# Make the src package tree importable when running via `uv run pytest`
# ---------------------------------------------------------------------------
_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from tools.project_knowledge import (  # noqa: E402
    ProjectKnowledgeInput,
    _build_filter,
    _normalise_chunk,
    search_project_knowledge,
)

# ---------------------------------------------------------------------------
# Fake Azure AI Search result objects
# ---------------------------------------------------------------------------


class _FakeSearchResult:
    """
    Mimics the attribute-access interface of azure.search.documents SearchResult.
    The @search.score key is exposed via __getattr__ to match SDK behaviour.
    """

    def __init__(
        self,
        content: str = (
            "Scour risk mitigation on pre-1970 concrete bridges requires "
            "detailed hydrological assessment per AASHTO LRFD 2020."
        ),
        source: str = "synthetic_risk_framework_bridge_scour_001",
        document_type: str = "risk_assessment_framework",
        domain: str = "transportation",
        score: float = 0.88,
        source_url=None,
        chunk_index: int = 0,
    ):
        self.content = content
        self.source = source
        self.document_type = document_type
        self.domain = domain
        self.source_url = source_url
        self.chunk_index = chunk_index
        self._score = score

    def __getattr__(self, item: str):
        if item == "@search.score":
            return self._score
        return None


# Fake 1536-dimension embedding vector (text-embedding-ada-002 dimension)
_FAKE_VECTOR = [0.01] * 1536


# ---------------------------------------------------------------------------
# Helper — patch _embed_query and Azure AI Search
# ---------------------------------------------------------------------------


def _patch_embedding():
    """Return a context manager that stubs out _embed_query to return _FAKE_VECTOR."""
    return patch(
        "tools.project_knowledge._embed_query",
        new=AsyncMock(return_value=_FAKE_VECTOR),
    )


def _patch_search(results):
    """Return (mock_client, cm_client, cm_cred, cm_vq) to stub AI Search.

    Since azure-search-documents is installed, SearchClient/AzureKeyCredential/
    VectorizedQuery are imported at module level in project_knowledge.py.
    We patch them in the tools.project_knowledge namespace.
    """
    mock_client = MagicMock()
    mock_client.search.return_value = iter(results)

    cm_client = patch("tools.project_knowledge.SearchClient", return_value=mock_client)
    cm_cred = patch("tools.project_knowledge.AzureKeyCredential", return_value=MagicMock())
    cm_vq = patch("tools.project_knowledge.VectorizedQuery", return_value=MagicMock())
    return mock_client, cm_client, cm_cred, cm_vq


# ---------------------------------------------------------------------------
# Tests — basic search behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_returns_chunks_with_required_fields():
    """search_project_knowledge must return >= 1 chunk with content, source,
    domain, and score fields present and non-None."""
    fake_results = [
        _FakeSearchResult(score=0.92),
        _FakeSearchResult(
            content="TWDB SWIFT loan program overview for water reuse projects.",
            source="synthetic_funding_guide_twdb_swift_001",
            document_type="funding_and_grants_guide",
            domain="water",
            score=0.85,
        ),
    ]

    mock_client, cm_client, cm_cred, cm_vq = _patch_search(fake_results)

    with _patch_embedding(), cm_client, cm_cred, cm_vq:
        inp = ProjectKnowledgeInput(query="scour risk on old bridges", top_k=6)
        result = await search_project_knowledge(inp)

    assert isinstance(result, list)
    assert len(result) >= 1

    for chunk in result:
        for field in ("content", "source", "domain", "score"):
            assert field in chunk, f"Chunk missing required field: {field}"
            assert chunk[field] is not None, f"Field '{field}' must not be None"

    assert result[0]["score"] > 0


@pytest.mark.asyncio
async def test_document_types_filter_applied():
    """Providing document_types must add an OData filter clause to the search.
    Specifically, 'document_type eq ...' must appear in the filter kwarg."""
    mock_client, cm_client, cm_cred, cm_vq = _patch_search([_FakeSearchResult()])

    with _patch_embedding(), cm_client, cm_cred, cm_vq:
        inp = ProjectKnowledgeInput(
            query="TWDB SWIFT funding program",
            document_types=["funding_and_grants_guide", "water_specific_market_intelligence"],
            top_k=4,
        )
        await search_project_knowledge(inp)

    call_kwargs = mock_client.search.call_args
    odata_filter = call_kwargs.kwargs.get("filter") or ""
    assert "document_type eq 'funding_and_grants_guide'" in odata_filter, (
        f"Expected document_type filter in OData expression, got: {odata_filter!r}"
    )
    assert "document_type eq 'water_specific_market_intelligence'" in odata_filter


@pytest.mark.asyncio
async def test_domains_filter_applied():
    """Providing domains must add a domain filter clause to the OData filter string."""
    mock_client, cm_client, cm_cred, cm_vq = _patch_search([_FakeSearchResult()])

    with _patch_embedding(), cm_client, cm_cred, cm_vq:
        inp = ProjectKnowledgeInput(
            query="grid resilience investment",
            domains=["energy", "transportation"],
            top_k=6,
        )
        await search_project_knowledge(inp)

    call_kwargs = mock_client.search.call_args
    odata_filter = call_kwargs.kwargs.get("filter") or ""
    assert "domain eq 'energy'" in odata_filter, (
        f"Expected domain filter in OData expression, got: {odata_filter!r}"
    )
    assert "domain eq 'transportation'" in odata_filter


@pytest.mark.asyncio
async def test_no_filters_produces_no_odata_filter():
    """When neither document_types nor domains are specified, the filter kwarg
    passed to SearchClient.search must be None."""
    mock_client, cm_client, cm_cred, cm_vq = _patch_search([_FakeSearchResult()])

    with _patch_embedding(), cm_client, cm_cred, cm_vq:
        inp = ProjectKnowledgeInput(query="general infrastructure consulting best practices")
        await search_project_knowledge(inp)

    call_kwargs = mock_client.search.call_args
    odata_filter = call_kwargs.kwargs.get("filter")
    assert odata_filter is None, (
        f"Expected None filter when no types/domains specified, got: {odata_filter!r}"
    )


@pytest.mark.asyncio
async def test_top_k_passed_to_search():
    """The top_k field must be forwarded as the 'top' kwarg to SearchClient.search."""
    mock_client, cm_client, cm_cred, cm_vq = _patch_search([_FakeSearchResult()] * 3)

    with _patch_embedding(), cm_client, cm_cred, cm_vq:
        inp = ProjectKnowledgeInput(query="bridge rehabilitation proposal", top_k=3)
        await search_project_knowledge(inp)

    call_kwargs = mock_client.search.call_args
    assert call_kwargs.kwargs.get("top") == 3


@pytest.mark.asyncio
async def test_empty_search_returns_empty_list():
    """When AI Search returns no results, the tool must return an empty list."""
    mock_client, cm_client, cm_cred, cm_vq = _patch_search([])

    with _patch_embedding(), cm_client, cm_cred, cm_vq:
        inp = ProjectKnowledgeInput(query="completely obscure query with no matches")
        result = await search_project_knowledge(inp)

    assert result == []


# ---------------------------------------------------------------------------
# Tests — DD metric emission
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dd_rag_metrics_emitted():
    """After a successful search, the tool must emit:
        rag.retrieval.top_score
        rag.retrieval.chunks_returned
        rag.index.last_updated"""
    fake_results = [_FakeSearchResult(score=0.93), _FakeSearchResult(score=0.78)]
    mock_client, cm_client, cm_cred, cm_vq = _patch_search(fake_results)
    mock_statsd = MagicMock()

    with _patch_embedding(), cm_client, cm_cred, cm_vq, patch(
        "observability.metrics.statsd", mock_statsd
    ):
        inp = ProjectKnowledgeInput(query="scour risk mitigation")
        await search_project_knowledge(inp)

    gauge_metric_names = [
        call.args[0] if call.args else call.kwargs.get("metric")
        for call in mock_statsd.gauge.call_args_list
    ]
    assert "rag.retrieval.top_score" in gauge_metric_names, (
        "rag.retrieval.top_score must be emitted as a DD gauge"
    )
    assert "rag.retrieval.chunks_returned" in gauge_metric_names, (
        "rag.retrieval.chunks_returned must be emitted as a DD gauge"
    )
    assert "rag.index.last_updated" in gauge_metric_names, (
        "rag.index.last_updated must be emitted as a DD gauge"
    )


@pytest.mark.asyncio
async def test_dd_tool_call_metrics_emitted():
    """mcp.tool.calls and mcp.tool.latency_ms must be emitted via DogStatsd
    on every successful tool invocation."""
    fake_results = [_FakeSearchResult()]
    mock_client, cm_client, cm_cred, cm_vq = _patch_search(fake_results)
    mock_statsd = MagicMock()

    with _patch_embedding(), cm_client, cm_cred, cm_vq, patch(
        "observability.metrics.statsd", mock_statsd
    ):
        inp = ProjectKnowledgeInput(query="desalination feasibility study SOW")
        await search_project_knowledge(inp)

    increment_calls = [str(c) for c in mock_statsd.increment.call_args_list]
    assert any("mcp.tool.calls" in c for c in increment_calls), (
        "Expected mcp.tool.calls to be incremented via DogStatsd"
    )

    gauge_calls = [str(c) for c in mock_statsd.gauge.call_args_list]
    assert any("mcp.tool.latency_ms" in c for c in gauge_calls), (
        "Expected mcp.tool.latency_ms to be emitted via DogStatsd"
    )


@pytest.mark.asyncio
async def test_top_score_value_correct():
    """The rag.retrieval.top_score gauge must equal the highest score in the result set."""
    fake_results = [
        _FakeSearchResult(score=0.72),
        _FakeSearchResult(score=0.95),  # this is the top score
        _FakeSearchResult(score=0.81),
    ]
    mock_client, cm_client, cm_cred, cm_vq = _patch_search(fake_results)
    mock_statsd = MagicMock()

    with _patch_embedding(), cm_client, cm_cred, cm_vq, patch(
        "observability.metrics.statsd", mock_statsd
    ):
        inp = ProjectKnowledgeInput(query="water reuse regulations Texas")
        await search_project_knowledge(inp)

    for call in mock_statsd.gauge.call_args_list:
        args = call.args if call.args else ()
        if args and args[0] == "rag.retrieval.top_score":
            assert abs(args[1] - 0.95) < 0.001, (
                f"top_score should be 0.95, got {args[1]}"
            )
            break
    else:
        pytest.fail("rag.retrieval.top_score gauge was not emitted")


# ---------------------------------------------------------------------------
# Tests — error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embedding_failure_returns_structured_error():
    """When the embedding call fails, search_project_knowledge must return a
    structured error dict (not raise an exception)."""
    with patch(
        "tools.project_knowledge._embed_query",
        new=AsyncMock(side_effect=RuntimeError("Azure OpenAI unavailable")),
    ):
        inp = ProjectKnowledgeInput(query="failed embedding test")
        result = await search_project_knowledge(inp)

    assert isinstance(result, dict), "Expected error dict when embedding fails"
    assert "error" in result
    assert result.get("retriable") is True


@pytest.mark.asyncio
async def test_search_client_failure_returns_structured_error():
    """When Azure AI Search raises an exception, the tool must return a structured
    error dict (not raise an exception)."""
    mock_client = MagicMock()
    mock_client.search.side_effect = Exception("Azure AI Search connection error")

    with _patch_embedding(), patch(
        "tools.project_knowledge.SearchClient", return_value=mock_client
    ), patch("tools.project_knowledge.AzureKeyCredential", return_value=MagicMock()), patch(
        "tools.project_knowledge.VectorizedQuery", return_value=MagicMock()
    ):
        inp = ProjectKnowledgeInput(query="connection failure test")
        result = await search_project_knowledge(inp)

    assert isinstance(result, dict)
    assert "error" in result
    assert result.get("source") == "azure_ai_search"


@pytest.mark.asyncio
async def test_missing_credentials_returns_structured_error(monkeypatch):
    """When AZURE_SEARCH_ENDPOINT or AZURE_SEARCH_API_KEY are absent, the tool
    must return a structured error dict immediately."""
    monkeypatch.setenv("AZURE_SEARCH_ENDPOINT", "")
    monkeypatch.setenv("AZURE_SEARCH_API_KEY", "")

    inp = ProjectKnowledgeInput(query="missing credentials test")
    result = await search_project_knowledge(inp)

    assert isinstance(result, dict)
    assert "error" in result
    assert result.get("retriable") is False


# ---------------------------------------------------------------------------
# Tests — _build_filter unit tests (no I/O)
# ---------------------------------------------------------------------------


def test_build_filter_with_both_filters():
    """_build_filter must include both document_type and domain clauses."""
    f = _build_filter(["risk_assessment_framework"], ["water"])
    assert f is not None
    assert "document_type eq 'risk_assessment_framework'" in f
    assert "domain eq 'water'" in f


def test_build_filter_no_filters_returns_none():
    """_build_filter with no arguments must return None."""
    assert _build_filter(None, None) is None


def test_build_filter_multiple_types():
    """Multiple document_types must be joined with 'or'."""
    f = _build_filter(["type_a", "type_b"], None)
    assert "document_type eq 'type_a'" in f
    assert "document_type eq 'type_b'" in f
    assert " or " in f


def test_build_filter_multiple_domains():
    """Multiple domains must be joined with 'or'."""
    f = _build_filter(None, ["water", "energy", "transportation"])
    assert "domain eq 'water'" in f
    assert "domain eq 'energy'" in f
    assert "domain eq 'transportation'" in f


# ---------------------------------------------------------------------------
# Tests — _normalise_chunk unit tests (no I/O)
# ---------------------------------------------------------------------------


def test_normalise_chunk_extracts_all_fields():
    """_normalise_chunk must extract all standard output fields."""
    fake = _FakeSearchResult(
        content="Test content about bridge scour.",
        source="synthetic_001",
        document_type="risk_assessment_framework",
        domain="transportation",
        score=0.77,
        source_url="https://kb.example.com/doc/001",
        chunk_index=2,
    )
    chunk = _normalise_chunk(fake, rank=0)

    assert chunk["content"] == "Test content about bridge scour."
    assert chunk["source"] == "synthetic_001"
    assert chunk["document_type"] == "risk_assessment_framework"
    assert chunk["domain"] == "transportation"
    assert abs(chunk["score"] - 0.77) < 0.001
    assert chunk["source_url"] == "https://kb.example.com/doc/001"
    assert chunk["chunk_index"] == 2
    assert "_retrieved_at" in chunk


def test_normalise_chunk_rank_used_when_no_chunk_index():
    """When a result has no chunk_index, the rank argument is used."""

    class _NoChunkIndex:
        content = "content"
        source = "src"
        document_type = "type"
        domain = "domain"
        source_url = None
        _score = 0.5

        def __getattr__(self, item):
            if item == "@search.score":
                return self._score
            raise AttributeError(item)

    chunk = _normalise_chunk(_NoChunkIndex(), rank=7)
    assert chunk["chunk_index"] == 7
