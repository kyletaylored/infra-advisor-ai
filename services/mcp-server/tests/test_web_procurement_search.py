"""Tests for the search_web_procurement tool (Tavily-backed).

All external HTTP calls are mocked with respx / monkeypatch so no real
credentials or network access are required.
"""

import os
import sys

import pytest

# ---------------------------------------------------------------------------
# Environment setup — must happen before any ddtrace imports
# ---------------------------------------------------------------------------
os.environ.setdefault("DD_AGENT_HOST", "localhost")
os.environ.setdefault("DD_TRACE_ENABLED", "false")
os.environ.setdefault("DD_DOGSTATSD_PORT", "8125")

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import respx
from httpx import Response

from tools.web_procurement_search import (
    TAVILY_SEARCH_URL,
    WebProcurementSearchInput,
    _build_search_query,
    search_web_procurement,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_TAVILY_RESPONSE = {
    "results": [
        {
            "url": "https://procurement.texas.gov/rfp/12345",
            "title": "TxDOT Bridge Rehabilitation RFP",
            "content": "TxDOT is soliciting proposals for bridge rehabilitation on IH-35. Deadline September 1, 2026. Contact rfp@txdot.gov. Estimated value $5,000,000.",
        },
        {
            "url": "https://www.twdb.texas.gov/projects/water-supply-rfp",
            "title": "TWDB Water Supply Project Solicitation",
            "content": "TWDB requests proposals for water supply infrastructure in Central Texas. Budget $2,500,000. Responses due October 15, 2026.",
        },
    ]
}

_EXTRACTED_RECORD = {
    "agency_name": "TxDOT",
    "project_title": "Bridge Rehabilitation IH-35",
    "project_description": "Rehabilitation of bridges on IH-35 corridor.",
    "estimated_value_usd": 5000000,
    "deadline": "2026-09-01",
    "contact_email": "rfp@txdot.gov",
    "source_url": "https://procurement.texas.gov/rfp/12345",
    "result_type": "rfp",
    "confidence": "high",
    "_source": "web_search",
    "_search_engine": "Tavily",
}


def _env_vars(monkeypatch) -> None:
    """Set required env vars for tests."""
    monkeypatch.setenv("TAVILY_API_KEY", "test-tavily-key")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://fake-openai.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "fake-openai-key")
    monkeypatch.setenv("AZURE_OPENAI_EVAL_DEPLOYMENT_NAME", "gpt-4.1-nano")


# ---------------------------------------------------------------------------
# Query construction tests (logic unchanged from Brave version)
# ---------------------------------------------------------------------------


def test_query_construction_with_site_hints():
    """_build_search_query should add site:.gov OR site:.us when geography is provided."""
    inp = WebProcurementSearchInput(query="bridge rehabilitation", geography="Texas")
    result = _build_search_query(inp)

    assert "bridge rehabilitation" in result
    assert "site:.gov OR site:.us" in result


def test_query_with_rfp_type():
    """_build_search_query should append RFP-related terms when result_type='rfp'."""
    inp = WebProcurementSearchInput(query="water treatment plant", result_type="rfp")
    result = _build_search_query(inp)

    assert "water treatment plant" in result
    assert '"request for proposals"' in result or "RFP" in result or "solicitation" in result


def test_query_sector_terms_appended():
    """Sector terms should be appended for known sectors."""
    inp = WebProcurementSearchInput(query="energy project", sector="transportation")
    result = _build_search_query(inp)

    assert "transportation infrastructure" in result


def test_query_bond_type():
    """Bond result_type should add bond-related search terms."""
    inp = WebProcurementSearchInput(query="Austin infrastructure", result_type="bond")
    result = _build_search_query(inp)

    assert '"bond election"' in result or '"municipal bond"' in result


def test_query_no_optional_fields():
    """With no optional fields, query should just be the raw query string."""
    inp = WebProcurementSearchInput(query="Texas infrastructure procurement")
    result = _build_search_query(inp)

    assert result == "Texas infrastructure procurement"


# ---------------------------------------------------------------------------
# Missing API key test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_tavily_api_key_returns_error(monkeypatch):
    """Without TAVILY_API_KEY, the tool should return a structured error dict."""
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    inp = WebProcurementSearchInput(query="Texas bridge RFP")
    result = await search_web_procurement(inp)

    assert isinstance(result, dict)
    assert "error" in result
    assert result.get("retriable") is False


# ---------------------------------------------------------------------------
# Tavily API error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tavily_api_error_returns_structured_error(monkeypatch):
    """A non-2xx response from Tavily should return a structured error dict."""
    _env_vars(monkeypatch)

    with respx.mock() as mock:
        mock.post(TAVILY_SEARCH_URL).mock(return_value=Response(401, text="Unauthorized"))

        inp = WebProcurementSearchInput(query="water treatment RFP", limit=4)
        result = await search_web_procurement(inp)

    assert isinstance(result, dict)
    assert "error" in result
    assert "Tavily" in result["error"]


# ---------------------------------------------------------------------------
# Content passed directly to extraction (no separate HTTP fetches)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_content_passed_directly_to_extraction(monkeypatch):
    """
    Verify that Tavily's content field is passed directly to _extract_procurement_data
    without any additional HTTP requests being made.
    """
    _env_vars(monkeypatch)

    captured_texts: list[str] = []

    import tools.web_procurement_search as mod

    def _fake_extract(text: str, source_url: str):
        captured_texts.append(text)
        rec = dict(_EXTRACTED_RECORD)
        rec["source_url"] = source_url
        return rec

    monkeypatch.setattr(mod, "_extract_procurement_data", _fake_extract)

    with respx.mock() as mock:
        # Only mock the Tavily endpoint — no page-fetch URLs registered
        mock.post(TAVILY_SEARCH_URL).mock(return_value=Response(200, json=_TAVILY_RESPONSE))

        inp = WebProcurementSearchInput(query="bridge RFP", geography="Texas", limit=4)
        result = await search_web_procurement(inp)

    # Extraction was called with the pre-fetched content from Tavily directly
    assert len(captured_texts) == 2
    assert "TxDOT is soliciting" in captured_texts[0]
    assert "TWDB requests proposals" in captured_texts[1]

    # Results are a plain list (no _partial_results envelope)
    assert isinstance(result, list)
    assert len(result) == 2
    for rec in result:
        assert rec["_source"] == "web_search"
        assert rec["_search_engine"] == "Tavily"


# ---------------------------------------------------------------------------
# Extraction filtering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extraction_skips_low_confidence(monkeypatch):
    """When _extract_procurement_data returns None (low confidence), record is excluded."""
    _env_vars(monkeypatch)

    import tools.web_procurement_search as mod

    monkeypatch.setattr(mod, "_extract_procurement_data", lambda text, url: None)

    with respx.mock() as mock:
        mock.post(TAVILY_SEARCH_URL).mock(return_value=Response(200, json=_TAVILY_RESPONSE))

        inp = WebProcurementSearchInput(query="bridge RFP", geography="Texas", limit=8)
        result = await search_web_procurement(inp)

    assert isinstance(result, list)
    assert len(result) == 0


# ---------------------------------------------------------------------------
# Happy-path end-to-end
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_end_to_end_extraction(monkeypatch):
    """Happy path: Tavily returns results with content, extraction produces records."""
    _env_vars(monkeypatch)

    import tools.web_procurement_search as mod

    def _fake_extract(text: str, source_url: str):
        rec = dict(_EXTRACTED_RECORD)
        rec["source_url"] = source_url
        return rec

    monkeypatch.setattr(mod, "_extract_procurement_data", _fake_extract)

    with respx.mock() as mock:
        mock.post(TAVILY_SEARCH_URL).mock(return_value=Response(200, json=_TAVILY_RESPONSE))

        inp = WebProcurementSearchInput(
            query="bridge rehabilitation",
            geography="Texas",
            sector="transportation",
            result_type="rfp",
            limit=8,
        )
        result = await search_web_procurement(inp)

    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0]["_source"] == "web_search"
    assert result[0]["_search_engine"] == "Tavily"
    assert "source_url" in result[0]


@pytest.mark.asyncio
async def test_empty_tavily_results_returns_empty_list(monkeypatch):
    """When Tavily returns no results, the tool returns an empty list."""
    _env_vars(monkeypatch)

    with respx.mock() as mock:
        mock.post(TAVILY_SEARCH_URL).mock(return_value=Response(200, json={"results": []}))

        inp = WebProcurementSearchInput(query="obscure Wyoming decommissioning RFP", limit=4)
        result = await search_web_procurement(inp)

    assert isinstance(result, list)
    assert len(result) == 0


# ---------------------------------------------------------------------------
# Request body verification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tavily_request_body(monkeypatch):
    """Verify the Tavily request is sent with correct fields including include_domains."""
    _env_vars(monkeypatch)

    import tools.web_procurement_search as mod
    monkeypatch.setattr(mod, "_extract_procurement_data", lambda text, url: None)

    captured_body: dict = {}

    def _capture_request(request):
        import json as _json
        captured_body.update(_json.loads(request.content))
        return Response(200, json={"results": []})

    with respx.mock() as mock:
        mock.post(TAVILY_SEARCH_URL).mock(side_effect=_capture_request)

        inp = WebProcurementSearchInput(query="water RFP Texas", limit=5)
        await search_web_procurement(inp)

    assert captured_body.get("search_depth") == "advanced"
    assert ".gov" in captured_body.get("include_domains", [])
    assert captured_body.get("max_results") == 5
    assert "api_key" in captured_body
