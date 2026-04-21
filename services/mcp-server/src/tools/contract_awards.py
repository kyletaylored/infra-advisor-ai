import ddtrace.auto  # must be first import — enables APM auto-instrumentation

import logging
import time
from datetime import date, timedelta
from typing import Any, List, Optional

import httpx
from pydantic import BaseModel

from observability.metrics import emit_external_api, emit_tool_call

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

USASPENDING_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"

_NAICS_MAP = {
    "water": ["237110"], "sewer": ["237110"],
    "bridge": ["237310"], "highway": ["237310"], "road": ["237310"], "transportation": ["237310"],
    "power": ["237130"], "energy": ["237130"],
    "pipeline": ["237120"],
    "building": ["236220"],
    "environmental": ["562910"],
    "dam": ["237990"], "flood": ["237990"],
}
_ALL_NAICS = list({code for codes in _NAICS_MAP.values() for code in codes})

_AWARD_TYPE_LABELS = {
    "A": "BPA Call",
    "B": "Purchase Order",
    "C": "Delivery Order",
    "D": "Definitive Contract",
}


# ---------------------------------------------------------------------------
# Input schema
# ---------------------------------------------------------------------------


class ContractAwardsInput(BaseModel):
    query: str
    geography: Optional[str] = None        # state abbreviation or city
    naics_codes: Optional[List[str]] = None
    agency_names: Optional[List[str]] = None
    date_from: Optional[str] = None        # ISO date string, defaults to 2 years ago
    date_to: Optional[str] = None          # ISO date string, defaults to today
    min_award_usd: Optional[int] = None
    limit: int = 25


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _derive_naics(query: str) -> list[str]:
    q = query.lower()
    codes: list[str] = []
    seen: set[str] = set()
    for term, term_codes in _NAICS_MAP.items():
        if term in q:
            for c in term_codes:
                if c not in seen:
                    codes.append(c)
                    seen.add(c)
    return codes if codes else list(_ALL_NAICS)


def _extract_state(geography: str) -> str | None:
    """Return a 2-letter state abbreviation from the geography string, or None."""
    g = geography.strip()
    if len(g) == 2 and g.isalpha():
        return g.upper()
    # Try to extract first 2-char alpha token from a city/state string like "Austin TX"
    for token in g.split():
        if len(token) == 2 and token.isalpha():
            return token.upper()
    return None


def _normalize_award(result: dict) -> dict[str, Any]:
    award_id = result.get("Award ID", "")
    return {
        "award_id": award_id,
        "recipient_name": result.get("Recipient Name", ""),
        "award_amount_usd": result.get("Award Amount") or result.get("Total Outlays"),
        "awarding_agency": result.get("Awarding Agency", ""),
        "awarding_sub_agency": result.get("Awarding Sub Agency", ""),
        "description": result.get("Description", ""),
        "place_of_performance": (
            f"{result.get('Place of Performance City Name', '')} "
            f"{result.get('Place of Performance State Code', '')}"
        ).strip(),
        "start_date": result.get("Start Date", ""),
        "end_date": result.get("End Date", ""),
        "naics_description": result.get("naics_description", ""),
        "contract_type": _AWARD_TYPE_LABELS.get(
            result.get("Contract Award Type", ""),
            result.get("Contract Award Type", ""),
        ),
        "usaspending_permalink": (
            f"https://www.usaspending.gov/award/{award_id}" if award_id else ""
        ),
        "_source": "USASpending.gov",
    }


# ---------------------------------------------------------------------------
# Public tool entry point
# ---------------------------------------------------------------------------


async def get_contract_awards(input_data: ContractAwardsInput) -> list | dict:
    """
    Query USASpending.gov for historical federal contract awards related to
    infrastructure projects.

    Returns a list of normalised award records on success, or a structured error dict.
    Never raises.
    """
    tool_start = time.monotonic()

    # Resolve date range
    today = date.today()
    date_from = input_data.date_from or (today - timedelta(days=730)).isoformat()
    date_to = input_data.date_to or today.isoformat()

    # Derive keywords from query (words longer than 3 chars)
    keywords = [w for w in input_data.query.split() if len(w) > 3]
    if not keywords:
        keywords = [input_data.query]

    # Resolve NAICS codes
    naics_codes = input_data.naics_codes if input_data.naics_codes else _derive_naics(input_data.query)

    # Build filters
    filters: dict[str, Any] = {
        "keywords": keywords,
        "time_period": [{"start_date": date_from, "end_date": date_to}],
        "award_type_codes": ["A", "B", "C", "D"],
        "naics_codes": naics_codes,
    }

    if input_data.geography:
        state_abbrev = _extract_state(input_data.geography)
        if state_abbrev:
            filters["place_of_performance_locations"] = [
                {"country": "USA", "state": state_abbrev}
            ]
        else:
            filters["place_of_performance_locations"] = []
    else:
        filters["place_of_performance_locations"] = []

    payload = {
        "filters": filters,
        "fields": [
            "Award ID",
            "Recipient Name",
            "recipient_id",
            "Award Amount",
            "Total Outlays",
            "Description",
            "Start Date",
            "End Date",
            "Awarding Agency",
            "Awarding Sub Agency",
            "Contract Award Type",
            "Place of Performance State Code",
            "Place of Performance City Name",
            "naics_code",
            "naics_description",
        ],
        "page": 1,
        "limit": input_data.limit,
        "sort": "Award Amount",
        "order": "desc",
    }

    api_start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(USASPENDING_URL, json=payload)
            api_latency_ms = (time.monotonic() - api_start) * 1000

            if resp.status_code >= 400:
                emit_external_api("usaspending", api_latency_ms, error_type=f"http_{resp.status_code}")
                emit_tool_call("get_contract_awards", (time.monotonic() - tool_start) * 1000, "error")
                return {
                    "error": f"USASpending API error: HTTP {resp.status_code}",
                    "retriable": resp.status_code >= 500,
                }

            emit_external_api("usaspending", api_latency_ms)
            body = resp.json()

    except httpx.TimeoutException:
        api_latency_ms = (time.monotonic() - api_start) * 1000
        emit_external_api("usaspending", api_latency_ms, error_type="timeout")
        emit_tool_call("get_contract_awards", (time.monotonic() - tool_start) * 1000, "error")
        return {"error": "USASpending API request timed out.", "retriable": True}

    except Exception:
        api_latency_ms = (time.monotonic() - api_start) * 1000
        emit_external_api("usaspending", api_latency_ms, error_type="unexpected")
        emit_tool_call("get_contract_awards", (time.monotonic() - tool_start) * 1000, "error")
        logger.exception("Unexpected error in get_contract_awards")
        return {"error": "Unexpected error querying USASpending.gov", "retriable": False}

    raw_results: list = body.get("results", [])

    if not raw_results:
        logger.info("USASpending returned zero results for query=%r", input_data.query)
        emit_tool_call("get_contract_awards", (time.monotonic() - tool_start) * 1000, "success", result_count=0)
        return []

    # Normalize
    awards = [_normalize_award(r) for r in raw_results]

    # Filter by min_award_usd
    if input_data.min_award_usd is not None:
        awards = [
            a for a in awards
            if a["award_amount_usd"] is not None and a["award_amount_usd"] >= input_data.min_award_usd
        ]

    # Filter by agency_names (case-insensitive substring match)
    if input_data.agency_names:
        agency_filters = [n.lower() for n in input_data.agency_names]
        awards = [
            a for a in awards
            if any(
                af in (a["awarding_agency"] or "").lower()
                or af in (a["awarding_sub_agency"] or "").lower()
                for af in agency_filters
            )
        ]

    emit_tool_call(
        "get_contract_awards", (time.monotonic() - tool_start) * 1000, "success", result_count=len(awards)
    )
    logger.info("USASpending returned %d awards for query=%r", len(awards), input_data.query)
    return awards
