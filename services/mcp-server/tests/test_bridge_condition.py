"""Tests for the get_bridge_condition tool.

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

from tools.bridge_condition import (
    BridgeConditionInput,
    NBI_ARCGIS_URL,
    get_bridge_condition,
)

# ---------------------------------------------------------------------------
# Shared fixture data
# ---------------------------------------------------------------------------

def _make_feature(
    structure_number: str = "4800200000B0001",
    deck: str = "4",
    superstructure: str = "3",
    substructure: str = "4",
    lowest_rating: int = 3,
    adt: int = 12_000,
    bridge_condition: str = "P",
) -> dict:
    return {
        "attributes": {
            "STRUCTURE_NUMBER_008": structure_number,
            "FACILITY_CARRIED_007": "US 281",
            "LOCATION_009": "0.5 MI N OF FM 2695",
            "COUNTY_CODE_003": "029",
            "STATE_CODE_001": "48",
            "ADT_029": adt,
            "YEAR_ADT_030": 2021,
            "DECK_COND_058": deck,
            "SUPERSTRUCTURE_COND_059": superstructure,
            "SUBSTRUCTURE_COND_060": substructure,
            "BRIDGE_CONDITION": bridge_condition,
            "LOWEST_RATING": lowest_rating,
            "SCOUR_CRITICAL_113": "8",
            "DATE_OF_INSPECT_090": "0621",
            "YEAR_BUILT_027": 1968,
            "LAT_016": 29.425,
            "LONG_017": -98.494,
        }
    }


def _arcgis_page(features: list, record_count: int | None = None) -> dict:
    """Build a minimal ArcGIS FeatureServer JSON response."""
    return {
        "features": features,
        "exceededTransferLimit": len(features) >= (record_count or 2000),
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_structurally_deficient_texas_bridges_returns_results():
    """Query for Texas structurally deficient bridges should return >=1 result
    with decoded condition labels present (e.g. 'poor', 'serious')."""
    features = [
        _make_feature(deck="4", superstructure="3", substructure="4"),
        _make_feature(structure_number="4800200000B0002", deck="3", superstructure="4"),
    ]

    with respx.mock(base_url=NBI_ARCGIS_URL) as mock:
        mock.get("").mock(return_value=Response(200, json=_arcgis_page(features)))

        inp = BridgeConditionInput(
            state_code="48",
            structurally_deficient_only=True,
            min_adt=10_000,
            limit=50,
        )
        result = await get_bridge_condition(inp)

    assert isinstance(result, list), "Expected a list of bridge dicts"
    assert len(result) >= 1

    first = result[0]
    # Condition labels must be decoded strings, not raw codes
    assert first["deck_condition"] in (
        "poor", "serious", "critical", "fair", "good", "satisfactory",
        "very good", "excellent", "imminent failure", "failed"
    ), f"Unexpected deck_condition: {first['deck_condition']}"
    assert first["superstructure_condition"] in (
        "poor", "serious", "critical", "fair", "good", "satisfactory",
        "very good", "excellent", "imminent failure", "failed"
    ), f"Unexpected superstructure_condition: {first['superstructure_condition']}"

    # Specifically: deck "4" → "poor", superstructure "3" → "serious"
    assert first["deck_condition"] == "poor"
    assert first["superstructure_condition"] == "serious"


@pytest.mark.asyncio
async def test_structure_number_present_in_result():
    """Every returned record must include the NBI structure number."""
    features = [_make_feature(structure_number="4800200000B0042")]

    with respx.mock(base_url=NBI_ARCGIS_URL) as mock:
        mock.get("").mock(return_value=Response(200, json=_arcgis_page(features)))

        inp = BridgeConditionInput(state_code="48", structure_number="4800200000B0042")
        result = await get_bridge_condition(inp)

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["structure_number"] == "4800200000B0042"


@pytest.mark.asyncio
async def test_pagination_fetches_multiple_pages():
    """When the first page returns exactly _ARCGIS_PAGE_SIZE records AND the
    requested limit has not been reached, the tool must fetch the next page.

    We temporarily reduce the internal page size constant so that a small
    feature set can exercise the pagination code path without needing 2000+
    fixture records.
    """
    import tools.bridge_condition as bc_module

    original_page_size = bc_module._ARCGIS_PAGE_SIZE
    bc_module._ARCGIS_PAGE_SIZE = 5  # tiny page size forces pagination after 5 items

    try:
        # Page 1: 5 items (== page size) → triggers pagination
        page1_features = [
            _make_feature(structure_number=f"4800200000T{i:04d}") for i in range(5)
        ]
        # Page 2: 1 item (< page size) → stops pagination
        page2_features = [_make_feature(structure_number="4800200000TLAST")]

        page1_response = {"features": page1_features, "exceededTransferLimit": True}
        page2_response = {"features": page2_features, "exceededTransferLimit": False}

        call_count = 0

        def _side_effect(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return Response(200, json=page1_response)
            return Response(200, json=page2_response)

        with respx.mock(base_url=NBI_ARCGIS_URL) as mock:
            mock.get("").mock(side_effect=_side_effect)

            inp = BridgeConditionInput(state_code="48", limit=10)
            result = await get_bridge_condition(inp)

    finally:
        bc_module._ARCGIS_PAGE_SIZE = original_page_size

    assert call_count >= 2, "Expected at least two HTTP requests (pagination)"
    assert isinstance(result, list)
    assert len(result) == 6  # 5 from page 1 + 1 from page 2


@pytest.mark.asyncio
async def test_api_error_returns_structured_error_dict():
    """A non-2xx HTTP response from BTS ArcGIS must return a structured error dict
    with 'error' and 'source' keys (not raise an exception)."""
    with respx.mock(base_url=NBI_ARCGIS_URL) as mock:
        mock.get("").mock(return_value=Response(500, text="Internal Server Error"))

        inp = BridgeConditionInput(state_code="48")
        result = await get_bridge_condition(inp)

    assert isinstance(result, dict), "Error response must be a dict"
    assert "error" in result, "Error dict must contain 'error' key"
    assert "source" in result, "Error dict must contain 'source' key"
    assert result["source"] == "bts_arcgis"
    assert "retriable" in result


@pytest.mark.asyncio
async def test_arcgis_json_error_returns_structured_error_dict():
    """An ArcGIS-layer error embedded in a 200 response must also return a structured error."""
    error_body = {
        "error": {
            "code": 400,
            "message": "Invalid or missing input parameters.",
            "details": [],
        }
    }

    with respx.mock(base_url=NBI_ARCGIS_URL) as mock:
        mock.get("").mock(return_value=Response(200, json=error_body))

        inp = BridgeConditionInput(state_code="48")
        result = await get_bridge_condition(inp)

    assert isinstance(result, dict)
    assert "error" in result
    assert result["source"] == "bts_arcgis"


@pytest.mark.asyncio
async def test_source_and_retrieved_at_metadata():
    """Every returned bridge record must include _source and _retrieved_at metadata."""
    features = [_make_feature()]

    with respx.mock(base_url=NBI_ARCGIS_URL) as mock:
        mock.get("").mock(return_value=Response(200, json=_arcgis_page(features)))

        inp = BridgeConditionInput(state_code="48")
        result = await get_bridge_condition(inp)

    assert isinstance(result, list) and len(result) == 1
    bridge = result[0]
    assert bridge["_source"] == "FHWA NBI"
    assert "_retrieved_at" in bridge
    # _retrieved_at must be a non-empty ISO 8601 string
    assert isinstance(bridge["_retrieved_at"], str)
    assert len(bridge["_retrieved_at"]) > 0


@pytest.mark.asyncio
async def test_empty_result_returns_empty_list():
    """A query that matches no bridges should return an empty list, not an error."""
    with respx.mock(base_url=NBI_ARCGIS_URL) as mock:
        mock.get("").mock(return_value=Response(200, json={"features": []}))

        inp = BridgeConditionInput(state_code="48", structure_number="NONEXISTENT")
        result = await get_bridge_condition(inp)

    assert result == []
