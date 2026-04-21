import ddtrace.auto  # must be first import — enables APM auto-instrumentation

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

import httpx
from pydantic import BaseModel

from observability.metrics import emit_external_api, emit_tool_call

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SAMGOV_API_URL = "https://api.sam.gov/opportunities/v2/search"
GRANTSGOV_SEARCH_URL = "https://apply07.grants.gov/grantsws/rest/opportunities/search/"

# NAICS codes relevant to infrastructure procurement domains
_NAICS_MAP: dict[str, list[str]] = {
    "water": ["237110"],
    "sewer": ["237110"],
    "bridge": ["237310"],
    "highway": ["237310"],
    "road": ["237310"],
    "transportation": ["237310"],
    "power": ["237130"],
    "energy": ["237130"],
    "pipeline": ["237120"],
    "building": ["236220"],
    "environmental": ["562910"],
    "dam": ["237990"],
    "flood": ["237990"],
}
_ALL_NAICS = list({code for codes in _NAICS_MAP.values() for code in codes})

# CFDA programs relevant to infrastructure domains
_CFDA_ALLOWLIST = {"66.458", "66.468", "97.047", "20.933", "14.228", "12.106", "11.300"}


# ---------------------------------------------------------------------------
# Input schema
# ---------------------------------------------------------------------------


class ProcurementOpportunitiesInput(BaseModel):
    query: str
    geography: Optional[str] = None
    naics_codes: Optional[List[str]] = None
    min_value_usd: Optional[int] = None
    max_value_usd: Optional[int] = None
    opportunity_types: Optional[List[str]] = None  # "contract", "grant", or both
    limit: int = 20


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _derive_naics(query: str) -> List[str]:
    """Derive relevant NAICS codes from the query text.

    Returns matched codes in order of first match, deduplicated.
    Falls back to all known infrastructure NAICS codes if no term matches.
    """
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


def _build_date_range(
    days_back: int = 365,
) -> tuple[str, str, bool]:
    """Return (postedFrom, postedTo, clamped) as mm/dd/yyyy strings.

    Always returns a range <= 365 days. If the requested range exceeds 365
    days, it is clamped and clamped=True is returned.
    """
    today = datetime.now(timezone.utc).date()
    posted_to = today
    posted_from = today - timedelta(days=days_back)

    clamped = False
    delta = (posted_to - posted_from).days
    if delta > 365:
        posted_to = posted_from + timedelta(days=365)
        clamped = True

    return (
        posted_from.strftime("%m/%d/%Y"),
        posted_to.strftime("%m/%d/%Y"),
        clamped,
    )


# ---------------------------------------------------------------------------
# SAM.gov fetch
# ---------------------------------------------------------------------------


async def _fetch_samgov(
    input_data: ProcurementOpportunitiesInput,
    naics_codes: List[str],
) -> list[dict[str, Any]] | dict[str, Any]:
    """Fetch contract opportunities from SAM.gov Opportunities API v2.

    Returns a list of normalised opportunity dicts, an empty-results dict,
    or a structured error dict. Never raises.
    """
    api_key = os.environ.get("SAMGOV_API_KEY", "")
    if not api_key:
        return {"error": "SAMGOV_API_KEY not configured", "retriable": False}

    posted_from, posted_to, clamped = _build_date_range(days_back=365)

    # Build params as list of tuples so httpx sends multiple ptype values
    params: list[tuple[str, str]] = [
        ("limit", "25"),
        ("offset", "0"),
        ("ptype", "o"),
        ("ptype", "p"),
        ("ptype", "k"),
        ("ptype", "r"),
        ("postedFrom", posted_from),
        ("postedTo", posted_to),
        ("api_key", api_key),
    ]

    for code in naics_codes:
        params.append(("ncode", code))

    if input_data.geography:
        params.append(("state", input_data.geography))

    api_start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(SAMGOV_API_URL, params=params)
            api_latency_ms = (time.monotonic() - api_start) * 1000

            if resp.status_code == 400:
                emit_external_api("samgov", api_latency_ms, error_type="http_400")
                try:
                    body = resp.json()
                    error_message = body.get("errorMessage") or body.get("errorCode", str(resp.status_code))
                except Exception:
                    error_message = resp.text

                if "Date range" in str(error_message):
                    return {
                        "error": (
                            "SAM.gov rejected the request: date range must be within 1 year. "
                            f"Raw message: {error_message}"
                        ),
                        "source": "samgov",
                        "retriable": False,
                    }
                return {
                    "error": f"SAM.gov API error 400: {error_message}",
                    "source": "samgov",
                    "retriable": False,
                }

            if resp.status_code == 403:
                emit_external_api("samgov", api_latency_ms, error_type="http_403")
                return {
                    "error": (
                        "SAM.gov API returned 403 — API key may need up to 24 hours to activate "
                        "after registration at api.sam.gov"
                    ),
                    "source": "samgov",
                    "retriable": False,
                }

            if resp.status_code >= 400:
                emit_external_api("samgov", api_latency_ms, error_type=f"http_{resp.status_code}")
                return {
                    "error": f"SAM.gov API error: HTTP {resp.status_code}",
                    "source": "samgov",
                    "retriable": resp.status_code >= 500,
                }

            emit_external_api("samgov", api_latency_ms)
            body = resp.json()

    except httpx.TimeoutException:
        api_latency_ms = (time.monotonic() - api_start) * 1000
        emit_external_api("samgov", api_latency_ms, error_type="timeout")
        raise  # re-raise so caller can handle partial results

    except httpx.RequestError as exc:
        api_latency_ms = (time.monotonic() - api_start) * 1000
        emit_external_api("samgov", api_latency_ms, error_type="request_error")
        logger.warning("SAM.gov request error: %s", exc)
        return {"error": f"SAM.gov request failed: {exc}", "source": "samgov", "retriable": True}

    except Exception:
        api_latency_ms = (time.monotonic() - api_start) * 1000
        emit_external_api("samgov", api_latency_ms, error_type="unexpected")
        logger.exception("Unexpected error in _fetch_samgov")
        return {"error": "Unexpected error querying SAM.gov", "source": "samgov", "retriable": False}

    if "opportunitiesData" not in body:
        logger.warning(
            "SAM.gov response missing 'opportunitiesData' key. Top-level keys: %s",
            list(body.keys()),
        )
        return {
            "error": "SAM.gov response format unexpected — 'opportunitiesData' key missing",
            "source": "samgov",
            "retriable": False,
            "response_keys": list(body.keys()),
        }

    opportunities = body["opportunitiesData"]
    if not opportunities:
        return {
            "results": [],
            "_note": f"No results found. NAICS codes queried: {naics_codes}",
        }

    results = []
    for opp in opportunities:
        results.append({
            "id": opp.get("noticeId") or opp.get("solicitationNumber", ""),
            "title": opp.get("title", ""),
            "type": opp.get("type", ""),
            "agency": opp.get("fullParentPathName") or opp.get("organizationName", ""),
            "naics_code": opp.get("naicsCode", ""),
            "posted_date": opp.get("postedDate", ""),
            "responseDeadLine": opp.get("responseDeadLine") or opp.get("archiveDate", ""),
            "award_value_usd": opp.get("award", {}).get("amount") if opp.get("award") else None,
            "description": (opp.get("description") or "")[:500],
            "url": opp.get("uiLink") or opp.get("resourceLinks", [None])[0],
            "place_of_performance": (opp.get("placeOfPerformance") or {}).get("stateName", ""),
            "_source": "SAM.gov",
        })

    if clamped:
        return {"results": results, "_note": "Date range clamped to 365 days maximum."}

    return results


# ---------------------------------------------------------------------------
# grants.gov fetch
# ---------------------------------------------------------------------------


async def _fetch_grantsgov(
    input_data: ProcurementOpportunitiesInput,
) -> list[dict[str, Any]]:
    """Fetch grant opportunities from grants.gov.

    Returns a list of normalised grant dicts (possibly empty). Never raises — on
    any error logs a warning and returns [].
    """
    api_start = time.monotonic()
    try:
        payload = {
            "keyword": input_data.query,
            "oppStatuses": "forecasted|posted",
            "rows": input_data.limit,
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                GRANTSGOV_SEARCH_URL,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            api_latency_ms = (time.monotonic() - api_start) * 1000
            emit_external_api("grantsgov", api_latency_ms)

            if resp.status_code >= 400:
                logger.warning("grants.gov API returned %s", resp.status_code)
                return []

            body = resp.json()

    except Exception as exc:
        api_latency_ms = (time.monotonic() - api_start) * 1000
        emit_external_api("grantsgov", api_latency_ms, error_type="error")
        logger.warning("grants.gov fetch error: %s", exc)
        return []

    raw_opportunities = body.get("opportunities") or body.get("data") or []
    results = []
    for opp in raw_opportunities:
        cfda_list = opp.get("cfdaList") or []
        # Filter to allowlisted CFDA programs relevant to infrastructure
        if not any(cfda.get("programNumber") in _CFDA_ALLOWLIST for cfda in cfda_list):
            continue

        results.append({
            "id": str(opp.get("id", "")),
            "title": opp.get("title", ""),
            "agency": opp.get("agencyName", ""),
            "open_date": opp.get("openDate", ""),
            "closeDate": opp.get("closeDate", ""),
            "estimated_total_funding_usd": opp.get("estimatedTotalProgramFunding"),
            "expected_awards": opp.get("expectedNumberOfAwards"),
            "description": (opp.get("description") or "")[:500],
            "cfda_numbers": [c.get("programNumber") for c in cfda_list],
            "_source": "grants.gov",
        })

    return results


# ---------------------------------------------------------------------------
# Public tool entry point
# ---------------------------------------------------------------------------


async def get_procurement_opportunities(
    input_data: ProcurementOpportunitiesInput,
) -> list[dict[str, Any]] | dict[str, Any]:
    """Search SAM.gov and grants.gov for infrastructure procurement opportunities.

    Queries both sources concurrently and merges results, sorted by deadline
    (soonest first). Supports filtering by geography, NAICS codes, and
    opportunity type ("contract", "grant", or both).

    Returns a unified list of opportunities or a structured error dict.
    Never raises.
    """
    tool_start = time.monotonic()

    naics_codes = input_data.naics_codes or _derive_naics(input_data.query)

    opportunity_types = input_data.opportunity_types or ["contract", "grant"]
    include_contracts = "contract" in opportunity_types
    include_grants = "grant" in opportunity_types

    async def _empty() -> list:
        return []

    # Schedule concurrent fetches (skip whichever source is not requested)
    samgov_coro = _fetch_samgov(input_data, naics_codes) if include_contracts else _empty()
    grantsgov_coro = _fetch_grantsgov(input_data) if include_grants else _empty()

    # Use asyncio.gather to run both fetches concurrently
    sam_result, grants_result = await asyncio.gather(
        samgov_coro, grantsgov_coro, return_exceptions=True
    )

    # Handle SAM.gov result
    sam_error: dict[str, Any] | None = None
    sam_items: list[dict[str, Any]] = []

    if isinstance(sam_result, BaseException):
        # TimeoutException or other exception from SAM.gov
        logger.warning("SAM.gov fetch raised exception: %s", sam_result)
        sam_error = {"error": f"SAM.gov request failed: {sam_result}", "source": "samgov", "retriable": True}
    elif isinstance(sam_result, dict):
        if "error" in sam_result:
            sam_error = sam_result
        elif "results" in sam_result:
            # Wrapped results (possibly clamped note or empty)
            sam_items = sam_result["results"]
        # else: unexpected dict shape, treat as empty
    elif isinstance(sam_result, list):
        sam_items = sam_result

    # Handle grants.gov result
    grants_items: list[dict[str, Any]] = []
    if isinstance(grants_result, BaseException):
        logger.warning("grants.gov fetch raised exception: %s", grants_result)
    elif isinstance(grants_result, list):
        grants_items = grants_result

    # Merge
    all_results = sam_items + grants_items

    # Sort by deadline: responseDeadLine for SAM.gov, closeDate for grants.gov
    def _deadline_key(item: dict) -> str:
        return item.get("responseDeadLine") or item.get("closeDate") or ""

    all_results.sort(key=_deadline_key)

    total_latency = (time.monotonic() - tool_start) * 1000

    if not all_results and sam_error:
        # Both sources failed or returned nothing, and SAM had a hard error
        emit_tool_call("get_procurement_opportunities", total_latency, "error")
        response: dict[str, Any] = sam_error.copy()
        return response

    emit_tool_call(
        "get_procurement_opportunities",
        total_latency,
        "success",
        result_count=len(all_results),
    )
    logger.info(
        "get_procurement_opportunities: %d results (SAM=%d, grants=%d)",
        len(all_results),
        len(sam_items),
        len(grants_items),
    )

    if sam_error and all_results:
        # Partial results — grants.gov succeeded but SAM.gov had an error
        return {
            "results": all_results,
            "_samgov_error": sam_error,
            "_note": "Partial results: SAM.gov unavailable, showing grants.gov results only.",
        }

    return all_results
