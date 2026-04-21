"""Tests for the get_contract_awards tool.

All external HTTP calls are mocked with respx so no real credentials
or network access are required (USASpending.gov is a public API — no auth needed).
"""

import os
import sys

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

from tools.contract_awards import (
    ContractAwardsInput,
    USASPENDING_URL,
    get_contract_awards,
)


# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------


def _make_award(
    award_id: str = "CONT_AWD_W9126G21C0011",
    recipient_name: str = "ACME CONSTRUCTION LLC",
    award_amount: float = 4_500_000.0,
    awarding_agency: str = "DEPARTMENT OF TRANSPORTATION",
    awarding_sub_agency: str = "FEDERAL HIGHWAY ADMINISTRATION",
    description: str = "BRIDGE REHABILITATION PROJECT",
    contract_type: str = "D",
    state_code: str = "TX",
    city_name: str = "Austin",
    naics_description: str = "Highway, Street, and Bridge Construction",
    start_date: str = "2023-01-15",
    end_date: str = "2024-06-30",
) -> dict:
    return {
        "Award ID": award_id,
        "Recipient Name": recipient_name,
        "recipient_id": "abc123",
        "Award Amount": award_amount,
        "Total Outlays": award_amount * 0.9,
        "Description": description,
        "Start Date": start_date,
        "End Date": end_date,
        "Awarding Agency": awarding_agency,
        "Awarding Sub Agency": awarding_sub_agency,
        "Contract Award Type": contract_type,
        "Place of Performance State Code": state_code,
        "Place of Performance City Name": city_name,
        "naics_code": "237310",
        "naics_description": naics_description,
    }


def _usaspending_response(awards: list) -> dict:
    """Build a minimal USASpending spending_by_award JSON response."""
    return {
        "results": awards,
        "page_metadata": {
            "page": 1,
            "count": len(awards),
            "next": None,
            "previous": None,
            "hasNext": False,
        },
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_successful_award_results():
    """Mock USASpending returning 2 awards. Assert both are normalized with the
    expected fields: recipient_name, award_amount_usd, _source, and a correct
    usaspending_permalink containing the award ID."""
    awards = [
        _make_award(award_id="CONT_AWD_001", recipient_name="BRIDGE CORP", award_amount=5_000_000.0),
        _make_award(award_id="CONT_AWD_002", recipient_name="ROAD BUILDERS INC", award_amount=2_000_000.0),
    ]

    with respx.mock as mock:
        mock.post(USASPENDING_URL).mock(
            return_value=Response(200, json=_usaspending_response(awards))
        )

        inp = ContractAwardsInput(query="bridge rehabilitation Texas")
        result = await get_contract_awards(inp)

    assert isinstance(result, list), "Expected a list of award dicts"
    assert len(result) == 2

    first = result[0]
    assert first["recipient_name"] == "BRIDGE CORP"
    assert first["award_amount_usd"] == 5_000_000.0
    assert first["_source"] == "USASpending.gov"
    assert "CONT_AWD_001" in first["usaspending_permalink"]

    second = result[1]
    assert second["recipient_name"] == "ROAD BUILDERS INC"
    assert second["award_amount_usd"] == 2_000_000.0
    assert second["_source"] == "USASpending.gov"
    assert "CONT_AWD_002" in second["usaspending_permalink"]


async def test_geography_filter_narrows_results():
    """When geography='TX' is passed, the request body sent to USASpending must
    include place_of_performance_locations with country=USA and state=TX."""
    awards = [
        _make_award(award_id="CONT_AWD_TX1", state_code="TX"),
        _make_award(award_id="CONT_AWD_TX2", state_code="TX"),
    ]

    captured_request_body: dict = {}

    def capture_and_respond(request):
        import json
        captured_request_body.update(json.loads(request.content))
        return Response(200, json=_usaspending_response(awards))

    with respx.mock as mock:
        mock.post(USASPENDING_URL).mock(side_effect=capture_and_respond)

        inp = ContractAwardsInput(query="highway construction", geography="TX")
        result = await get_contract_awards(inp)

    assert isinstance(result, list)
    assert len(result) == 2

    # Verify the request body included the geography filter
    locations = captured_request_body["filters"]["place_of_performance_locations"]
    assert len(locations) == 1
    assert locations[0]["country"] == "USA"
    assert locations[0]["state"] == "TX"


async def test_api_error_returns_structured_error():
    """Mock USASpending returning 500. Assert result is a dict with 'error' key
    and 'retriable': True (server error is considered retriable)."""
    with respx.mock as mock:
        mock.post(USASPENDING_URL).mock(
            return_value=Response(500, text="Internal Server Error")
        )

        inp = ContractAwardsInput(query="water infrastructure")
        result = await get_contract_awards(inp)

    assert isinstance(result, dict), "Error response must be a dict"
    assert "error" in result, "Error dict must contain 'error' key"
    assert "USASpending API error: HTTP 500" in result["error"]
    assert result["retriable"] is True
