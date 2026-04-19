"""Tests for the get_disaster_history tool.

All external HTTP calls are mocked with respx so no real credentials
or network access are required.
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

# Make sure the src package tree is importable when running from the
# services/mcp-server root via `uv run pytest`.
_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import respx
from httpx import Response

from tools.disaster_history import (
    DisasterHistoryInput,
    OPENFEMA_URL,
    get_disaster_history,
)

# ---------------------------------------------------------------------------
# Shared fixture data
# ---------------------------------------------------------------------------

def _make_declaration(
    disaster_number: int = 4000,
    state: str = "TX",
    incident_type: str = "Flood",
    title: str = "SEVERE STORMS AND FLOODING",
    declaration_date: str = "2019-10-01T00:00:00.000z",
) -> dict:
    return {
        "disasterNumber": disaster_number,
        "declarationType": "DR",
        "declarationTitle": title,
        "incidentType": incident_type,
        "state": state,
        "designatedArea": "Harris (County)",
        "declarationDate": declaration_date,
        "incidentBeginDate": "2019-09-19T00:00:00.000z",
        "incidentEndDate": "2019-09-26T00:00:00.000z",
        "closeOutDate": None,
        "fipsStateCode": "48",
        "fipsCountyCode": "201",
        "ihProgramDeclared": True,
        "iaProgramDeclared": True,
        "paProgramDeclared": True,
        "hmProgramDeclared": True,
    }


def _fema_page(declarations: list) -> dict:
    return {"DisasterDeclarationsSummaries": declarations}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gulf_coast_flood_declarations_returns_results():
    """Query for Gulf Coast states flood declarations should return >=1 result."""
    declarations = [
        _make_declaration(state="TX", incident_type="Flood"),
        _make_declaration(disaster_number=4001, state="LA", incident_type="Flood"),
        _make_declaration(disaster_number=4002, state="MS", incident_type="Flood"),
    ]

    with respx.mock(base_url=OPENFEMA_URL) as mock:
        mock.get("").mock(return_value=Response(200, json=_fema_page(declarations)))

        inp = DisasterHistoryInput(
            states=["TX", "LA", "MS"],
            incident_types=["Flood"],
            limit=100,
        )
        result = await get_disaster_history(inp)

    assert isinstance(result, list)
    assert len(result) >= 1
    # All returned records should have _source set correctly
    for rec in result:
        assert rec["_source"] == "OpenFEMA"
        assert "_retrieved_at" in rec


@pytest.mark.asyncio
async def test_empty_result_handled_gracefully():
    """When OpenFEMA returns zero records the tool must return an empty list,
    not an error dict."""
    with respx.mock(base_url=OPENFEMA_URL) as mock:
        mock.get("").mock(return_value=Response(200, json=_fema_page([])))

        inp = DisasterHistoryInput(states=["WY"], incident_types=["Tornado"], limit=10)
        result = await get_disaster_history(inp)

    assert result == [], f"Expected empty list, got: {result}"


@pytest.mark.asyncio
async def test_infrastructure_keywords_filter():
    """infrastructure_keywords should filter declarations client-side by declarationTitle."""
    declarations = [
        _make_declaration(
            disaster_number=5001,
            title="SEVERE STORMS AND FLOODING — BRIDGE AND HIGHWAY DAMAGE",
        ),
        _make_declaration(
            disaster_number=5002,
            title="HURRICANE DAMAGE — RESIDENTIAL AREAS",
        ),
        _make_declaration(
            disaster_number=5003,
            title="FLOODING — WATER TREATMENT PLANT FAILURE",
        ),
    ]

    with respx.mock(base_url=OPENFEMA_URL) as mock:
        mock.get("").mock(return_value=Response(200, json=_fema_page(declarations)))

        inp = DisasterHistoryInput(
            states=["TX"],
            infrastructure_keywords=["bridge", "water treatment"],
            limit=100,
        )
        result = await get_disaster_history(inp)

    assert isinstance(result, list)
    # Only the two infrastructure-related declarations should pass the filter
    assert len(result) == 2
    returned_numbers = {r["disaster_number"] for r in result}
    assert 5001 in returned_numbers
    assert 5003 in returned_numbers
    assert 5002 not in returned_numbers


@pytest.mark.asyncio
async def test_infrastructure_keywords_no_match_returns_empty_list():
    """When no declaration titles match the keywords the result must be an empty list."""
    declarations = [
        _make_declaration(title="HURRICANE DAMAGE — RESIDENTIAL AREAS"),
    ]

    with respx.mock(base_url=OPENFEMA_URL) as mock:
        mock.get("").mock(return_value=Response(200, json=_fema_page(declarations)))

        inp = DisasterHistoryInput(
            states=["TX"],
            infrastructure_keywords=["pipeline", "reservoir"],
            limit=10,
        )
        result = await get_disaster_history(inp)

    assert result == []


@pytest.mark.asyncio
async def test_api_error_returns_structured_error_dict():
    """A non-2xx HTTP response from OpenFEMA must return a structured error dict
    with 'error' and 'source' keys (not raise an exception)."""
    with respx.mock(base_url=OPENFEMA_URL) as mock:
        mock.get("").mock(return_value=Response(503, text="Service Unavailable"))

        inp = DisasterHistoryInput(states=["TX"])
        result = await get_disaster_history(inp)

    assert isinstance(result, dict), "Error response must be a dict"
    assert "error" in result
    assert "source" in result
    assert result["source"] == "openfema"
    assert "retriable" in result
    assert result["retriable"] is True  # 503 is retriable


@pytest.mark.asyncio
async def test_pagination_fetches_multiple_pages():
    """When the first page returns exactly _FEMA_PAGE_SIZE records AND the
    requested limit has not been reached, the tool must fetch the next page.

    We temporarily reduce the internal page size constant so that a small
    fixture set can exercise the pagination code path.
    """
    import tools.disaster_history as dh_module

    original_page_size = dh_module._FEMA_PAGE_SIZE
    dh_module._FEMA_PAGE_SIZE = 3  # tiny page size forces pagination after 3 items

    try:
        # Page 1: 3 items (== page size) → triggers pagination
        page1 = [_make_declaration(disaster_number=i, state="TX") for i in range(3)]
        # Page 2: 1 item (< page size) → stops pagination
        page2 = [_make_declaration(disaster_number=9999, state="TX")]

        call_count = 0

        def _side_effect(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return Response(200, json=_fema_page(page1))
            return Response(200, json=_fema_page(page2))

        with respx.mock(base_url=OPENFEMA_URL) as mock:
            mock.get("").mock(side_effect=_side_effect)

            inp = DisasterHistoryInput(states=["TX"], limit=10)
            result = await get_disaster_history(inp)

    finally:
        dh_module._FEMA_PAGE_SIZE = original_page_size

    assert call_count >= 2, "Expected at least two HTTP requests (pagination)"
    assert isinstance(result, list)
    assert len(result) == 4  # 3 from page 1 + 1 from page 2


@pytest.mark.asyncio
async def test_result_fields_present():
    """Each returned declaration must include key normalised fields."""
    declarations = [
        _make_declaration(
            disaster_number=4321,
            state="LA",
            incident_type="Flood",
            title="HISTORIC FLOODING",
            declaration_date="2016-08-14T00:00:00.000z",
        )
    ]

    with respx.mock(base_url=OPENFEMA_URL) as mock:
        mock.get("").mock(return_value=Response(200, json=_fema_page(declarations)))

        inp = DisasterHistoryInput(states=["LA"], incident_types=["Flood"])
        result = await get_disaster_history(inp)

    assert len(result) == 1
    rec = result[0]

    for field in (
        "disaster_number",
        "declaration_title",
        "incident_type",
        "state",
        "declaration_date",
        "_source",
        "_retrieved_at",
    ):
        assert field in rec, f"Missing field: {field}"

    assert rec["disaster_number"] == 4321
    assert rec["state"] == "LA"
    assert rec["incident_type"] == "Flood"
    assert rec["_source"] == "OpenFEMA"
