"""Tests for the search_web_procurement tool (Azure OpenAI Responses API).

All external HTTP calls are mocked with respx so no real Azure OpenAI
credentials or network access are required. We stub the responses endpoint
to return the schema-constrained JSON the production model would emit.
"""

import os
import sys
from unittest.mock import patch

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
    WebProcurementSearchInput,
    _build_instructions,
    search_web_procurement,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_AZURE_ENDPOINT = "https://fake-openai.openai.azure.com"
_RESPONSES_URL = f"{_AZURE_ENDPOINT}/openai/v1/responses"

# Two-item happy-path payload matching the Azure Responses API shape +
# our procurement_results json_schema envelope.
_RESPONSES_PAYLOAD = {
    "output": [
        {
            "type": "message",
            "content": [{
                "type": "output_text",
                "text": (
                    '{"results":['
                    '{"agency_name":"TxDOT",'
                    '"project_title":"Bridge Rehabilitation IH-35",'
                    '"project_description":"Rehabilitation on IH-35.",'
                    '"estimated_value_usd":5000000,'
                    '"deadline":"2026-09-01",'
                    '"contact_email":"rfp@txdot.gov",'
                    '"source_url":"https://procurement.texas.gov/rfp/12345",'
                    '"result_type":"rfp","confidence":"high"},'
                    '{"agency_name":"TWDB",'
                    '"project_title":"Water Supply Solicitation",'
                    '"project_description":null,'
                    '"estimated_value_usd":2500000,'
                    '"deadline":"2026-10-15",'
                    '"contact_email":null,'
                    '"source_url":"https://www.twdb.texas.gov/projects/water-supply-rfp",'
                    '"result_type":"rfp","confidence":"medium"}'
                    ']}'
                ),
            }],
        }
    ]
}


def _env_vars(monkeypatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", _AZURE_ENDPOINT)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "fake-openai-key")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4.1-mini")


# ---------------------------------------------------------------------------
# Instruction-building tests
# ---------------------------------------------------------------------------


def test_instructions_include_query_and_geography():
    inp = WebProcurementSearchInput(query="bridge rehabilitation", geography="Texas")
    out = _build_instructions(inp)
    assert "bridge rehabilitation" in out
    assert "in Texas" in out
    # Default phrasing when no result_type given
    assert "procurement opportunities" in out


def test_instructions_for_rfp_type():
    inp = WebProcurementSearchInput(query="water treatment plant", result_type="rfp")
    out = _build_instructions(inp)
    assert "requests for proposals" in out.lower()


def test_instructions_for_bond_type():
    inp = WebProcurementSearchInput(query="Austin infrastructure", result_type="bond")
    out = _build_instructions(inp)
    assert "bond" in out.lower()


def test_instructions_apply_sector_term():
    inp = WebProcurementSearchInput(query="energy project", sector="transportation")
    out = _build_instructions(inp)
    assert "transportation infrastructure" in out


def test_instructions_default_no_optional_fields():
    inp = WebProcurementSearchInput(query="Texas infrastructure procurement")
    out = _build_instructions(inp)
    assert "Texas infrastructure procurement" in out
    # No "in <geography>" qualifier when geography is unset
    assert " in " not in out.split("Search the web")[1][:200]


# ---------------------------------------------------------------------------
# Configuration-error tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_azure_endpoint_returns_error(monkeypatch):
    """Without AZURE_OPENAI_ENDPOINT, the tool returns a structured error dict."""
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)

    inp = WebProcurementSearchInput(query="Texas bridge RFP")
    result = await search_web_procurement(inp)

    assert isinstance(result, dict)
    assert "error" in result
    assert result.get("retriable") is False
    assert result.get("source") == "azure_openai"


# ---------------------------------------------------------------------------
# Azure OpenAI error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_responses_api_error_returns_structured_error(monkeypatch):
    """A non-2xx Azure Responses response yields a structured error dict."""
    _env_vars(monkeypatch)

    with respx.mock() as mock:
        mock.post(url__startswith=_RESPONSES_URL).mock(
            return_value=Response(401, text="Unauthorized")
        )

        inp = WebProcurementSearchInput(query="water treatment RFP", limit=4)
        result = await search_web_procurement(inp)

    assert isinstance(result, dict)
    assert "error" in result
    assert "Azure OpenAI" in result["error"]
    assert result["source"] == "azure_openai"


@pytest.mark.asyncio
async def test_5xx_marked_retriable(monkeypatch):
    """5xx responses should be flagged retriable=True so the caller may retry."""
    _env_vars(monkeypatch)

    with respx.mock() as mock:
        mock.post(url__startswith=_RESPONSES_URL).mock(
            return_value=Response(503, text="Unavailable")
        )

        inp = WebProcurementSearchInput(query="water treatment RFP")
        result = await search_web_procurement(inp)

    assert isinstance(result, dict)
    assert result.get("retriable") is True


@pytest.mark.asyncio
async def test_429_marked_retriable(monkeypatch):
    """Rate-limit (429) should be flagged retriable=True."""
    _env_vars(monkeypatch)

    with respx.mock() as mock:
        mock.post(url__startswith=_RESPONSES_URL).mock(
            return_value=Response(429, text="Too Many Requests")
        )

        inp = WebProcurementSearchInput(query="water treatment RFP")
        result = await search_web_procurement(inp)

    assert isinstance(result, dict)
    assert result.get("retriable") is True


# ---------------------------------------------------------------------------
# Happy-path end-to-end
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_end_to_end(monkeypatch):
    """Happy path: Azure OpenAI returns structured JSON, tool unwraps the results array."""
    _env_vars(monkeypatch)

    with respx.mock() as mock:
        mock.post(url__startswith=_RESPONSES_URL).mock(
            return_value=Response(200, json=_RESPONSES_PAYLOAD)
        )

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
    assert result[0]["agency_name"] == "TxDOT"
    assert result[0]["source_url"] == "https://procurement.texas.gov/rfp/12345"
    assert result[1]["confidence"] == "medium"


@pytest.mark.asyncio
async def test_empty_results_returned_as_empty_list(monkeypatch):
    """When the model finds nothing, an empty results array yields an empty list."""
    _env_vars(monkeypatch)

    empty_payload = {
        "output": [{
            "type": "message",
            "content": [{"type": "output_text", "text": '{"results":[]}'}],
        }]
    }

    with respx.mock() as mock:
        mock.post(url__startswith=_RESPONSES_URL).mock(
            return_value=Response(200, json=empty_payload)
        )

        inp = WebProcurementSearchInput(query="obscure Wyoming decommissioning RFP")
        result = await search_web_procurement(inp)

    assert isinstance(result, list)
    assert len(result) == 0


@pytest.mark.asyncio
async def test_no_output_text_returns_empty_list(monkeypatch):
    """If the API returns no output_text content, treat as empty results."""
    _env_vars(monkeypatch)

    no_text_payload = {"output": [{"type": "message", "content": []}]}

    with respx.mock() as mock:
        mock.post(url__startswith=_RESPONSES_URL).mock(
            return_value=Response(200, json=no_text_payload)
        )

        inp = WebProcurementSearchInput(query="anything")
        result = await search_web_procurement(inp)

    assert isinstance(result, list)
    assert len(result) == 0


@pytest.mark.asyncio
async def test_malformed_json_returns_structured_error(monkeypatch):
    """Malformed JSON output from the model returns a structured error dict."""
    _env_vars(monkeypatch)

    bad_payload = {
        "output": [{
            "type": "message",
            "content": [{"type": "output_text", "text": "not valid json"}],
        }]
    }

    with respx.mock() as mock:
        mock.post(url__startswith=_RESPONSES_URL).mock(
            return_value=Response(200, json=bad_payload)
        )

        inp = WebProcurementSearchInput(query="anything")
        result = await search_web_procurement(inp)

    assert isinstance(result, dict)
    assert "error" in result
    assert "malformed" in result["error"].lower()


@pytest.mark.asyncio
async def test_malformed_json_logs_the_actual_text_that_failed_to_parse(monkeypatch):
    """Regression test: the exact gap from the user's bug report — malformed
    JSON from the model previously logged only 'failed to parse model JSON',
    never the actual text that failed json.loads(). Must now log it via
    log_external_api_failure so it's recoverable from Datadog."""
    _env_vars(monkeypatch)

    bad_payload = {
        "output": [{
            "type": "message",
            "content": [{"type": "output_text", "text": "not valid json {broken"}],
        }]
    }

    with (
        patch("tools.web_procurement_search.log_external_api_failure") as mock_log,
        respx.mock() as mock,
    ):
        mock.post(url__startswith=_RESPONSES_URL).mock(
            return_value=Response(200, json=bad_payload)
        )

        inp = WebProcurementSearchInput(query="anything")
        await search_web_procurement(inp)

    mock_log.assert_called_once()
    kwargs = mock_log.call_args.kwargs
    assert kwargs["source"] == "azure_openai"
    assert kwargs["body"] == "not valid json {broken"


# ---------------------------------------------------------------------------
# Request body verification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_body_includes_web_search_tool(monkeypatch):
    """Verify the request includes the web_search_preview tool + json_schema response format."""
    _env_vars(monkeypatch)

    captured_body: dict = {}

    def _capture_request(request):
        import json as _json
        captured_body.update(_json.loads(request.content))
        return Response(200, json={"output": [{"type": "message", "content": [
            {"type": "output_text", "text": '{"results":[]}'}
        ]}]})

    with respx.mock() as mock:
        mock.post(url__startswith=_RESPONSES_URL).mock(side_effect=_capture_request)

        inp = WebProcurementSearchInput(query="water RFP Texas", limit=5)
        await search_web_procurement(inp)

    assert captured_body.get("model") == "gpt-4.1-mini"
    assert isinstance(captured_body.get("input"), str)
    assert "water RFP Texas" in captured_body["input"]
    tools = captured_body.get("tools", [])
    assert any(t.get("type") == "web_search_preview" for t in tools)
    text_format = captured_body.get("text", {}).get("format", {})
    assert text_format.get("type") == "json_schema"
    assert text_format.get("name") == "procurement_results"
    assert text_format.get("strict") is True


@pytest.mark.asyncio
async def test_request_uses_api_key_header(monkeypatch):
    """The Azure OpenAI api-key header must be set on the outbound request."""
    _env_vars(monkeypatch)

    captured_headers: dict = {}

    def _capture_request(request):
        captured_headers.update(dict(request.headers))
        return Response(200, json={"output": [{"type": "message", "content": [
            {"type": "output_text", "text": '{"results":[]}'}
        ]}]})

    with respx.mock() as mock:
        mock.post(url__startswith=_RESPONSES_URL).mock(side_effect=_capture_request)

        inp = WebProcurementSearchInput(query="anything")
        await search_web_procurement(inp)

    # httpx normalizes header keys to lowercase
    assert captured_headers.get("api-key") == "fake-openai-key"
