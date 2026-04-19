import ddtrace.auto  # must be first import

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from pydantic import BaseModel, Field

from observability.metrics import emit_external_api, emit_tool_call

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ArcGIS / BTS NBI endpoint
# ---------------------------------------------------------------------------
NBI_ARCGIS_URL = (
    "https://services.arcgis.com/xOi1kZaI0eWDREZv/arcgis/rest/services"
    "/National_Bridge_Inventory/FeatureServer/0/query"
)

# Fields to request from the feature server (exact NBI field names per PRD §3)
NBI_OUTFIELDS = ",".join(
    [
        "STRUCTURE_NUMBER_008",
        "FACILITY_CARRIED_007",
        "LOCATION_009",
        "COUNTY_CODE_003",
        "STATE_CODE_001",
        "ADT_029",
        "YEAR_ADT_030",
        "DECK_COND_058",
        "SUPERSTRUCTURE_COND_059",
        "SUBSTRUCTURE_COND_060",
        "STRUCTURALLY_DEFICIENT",
        "SUFFICIENCY_RATING",
        "INSPECT_DATE_090",
        "YEAR_BUILT_027",
        "LAT_016",
        "LONG_017",
    ]
)

# Condition code → human label per PRD §3
CONDITION_LABELS: dict[str, str] = {
    "9": "excellent",
    "8": "very good",
    "7": "good",
    "6": "satisfactory",
    "5": "fair",
    "4": "poor",
    "3": "serious",
    "2": "critical",
    "1": "imminent failure",
    "0": "failed",
}

# ArcGIS max records per page
_ARCGIS_PAGE_SIZE = 2000


# ---------------------------------------------------------------------------
# Input model
# ---------------------------------------------------------------------------
class BridgeConditionInput(BaseModel):
    """Input parameters for the get_bridge_condition tool."""

    state_code: str = Field(..., description="2-digit FIPS state code (TX=48)")
    county_code: Optional[str] = Field(
        default=None, description="3-digit county FIPS code"
    )
    structure_number: Optional[str] = Field(
        default=None, description="Exact NBI structure number for a single-bridge lookup"
    )
    min_adt: Optional[int] = Field(
        default=None, description="Minimum average daily traffic"
    )
    max_sufficiency_rating: Optional[float] = Field(
        default=None, description="Upper bound on FHWA sufficiency rating (0–100)"
    )
    structurally_deficient_only: bool = Field(
        default=False,
        description="When True, restricts results to structurally deficient bridges",
    )
    last_inspection_before: Optional[str] = Field(
        default=None,
        description="ISO date string — return bridges last inspected before this date",
    )
    order_by: str = Field(
        default="SUFFICIENCY_RATING ASC",
        description="ORDER BY clause for the ArcGIS query",
    )
    limit: int = Field(
        default=50,
        ge=1,
        le=200,
        description="Maximum number of bridges to return (1–200)",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_where_clause(inp: BridgeConditionInput) -> str:
    """Build an ArcGIS SQL WHERE clause from the validated input model."""
    clauses: list[str] = []

    clauses.append(f"STATE_CODE_001='{inp.state_code}'")

    if inp.county_code:
        clauses.append(f"COUNTY_CODE_003='{inp.county_code}'")

    if inp.structure_number:
        clauses.append(f"STRUCTURE_NUMBER_008='{inp.structure_number}'")

    if inp.min_adt is not None:
        clauses.append(f"ADT_029>={inp.min_adt}")

    if inp.max_sufficiency_rating is not None:
        clauses.append(f"SUFFICIENCY_RATING<={inp.max_sufficiency_rating}")

    if inp.structurally_deficient_only:
        clauses.append("STRUCTURALLY_DEFICIENT='1'")

    if inp.last_inspection_before:
        # ArcGIS expects dates as epoch milliseconds in WHERE clauses when the
        # field is a Date type; for string comparisons use ISO format directly.
        # The NBI INSPECT_DATE_090 field is stored as a Date — use TIMESTAMP keyword.
        clauses.append(f"INSPECT_DATE_090 < TIMESTAMP '{inp.last_inspection_before} 00:00:00'")

    return " AND ".join(clauses) if clauses else "1=1"


def _decode_condition(raw: Any) -> str | None:
    """Decode a raw NBI condition code (0–9 or N) to its human label."""
    if raw is None:
        return None
    key = str(raw).strip()
    return CONDITION_LABELS.get(key)


def _normalise_feature(attrs: dict[str, Any], retrieved_at: str) -> dict[str, Any]:
    """Convert a raw ArcGIS feature attributes dict to a normalised bridge record."""
    return {
        "structure_number": attrs.get("STRUCTURE_NUMBER_008"),
        "facility_carried": attrs.get("FACILITY_CARRIED_007"),
        "location": attrs.get("LOCATION_009"),
        "state_code": attrs.get("STATE_CODE_001"),
        "county_code": attrs.get("COUNTY_CODE_003"),
        "adt": attrs.get("ADT_029"),
        "year_adt": attrs.get("YEAR_ADT_030"),
        "deck_condition_code": attrs.get("DECK_COND_058"),
        "deck_condition": _decode_condition(attrs.get("DECK_COND_058")),
        "superstructure_condition_code": attrs.get("SUPERSTRUCTURE_COND_059"),
        "superstructure_condition": _decode_condition(attrs.get("SUPERSTRUCTURE_COND_059")),
        "substructure_condition_code": attrs.get("SUBSTRUCTURE_COND_060"),
        "substructure_condition": _decode_condition(attrs.get("SUBSTRUCTURE_COND_060")),
        "structurally_deficient": attrs.get("STRUCTURALLY_DEFICIENT") == "1",
        "sufficiency_rating": attrs.get("SUFFICIENCY_RATING"),
        "last_inspection_date": attrs.get("INSPECT_DATE_090"),
        "year_built": attrs.get("YEAR_BUILT_027"),
        "latitude": attrs.get("LAT_016"),
        "longitude": attrs.get("LONG_017"),
        "_source": "FHWA NBI",
        "_retrieved_at": retrieved_at,
    }


# ---------------------------------------------------------------------------
# Core async implementation
# ---------------------------------------------------------------------------

async def _fetch_page(
    client: httpx.AsyncClient,
    where: str,
    order_by: str,
    offset: int,
) -> dict[str, Any]:
    """Fetch a single page from the ArcGIS feature server."""
    params = {
        "where": where,
        "outFields": NBI_OUTFIELDS,
        "orderByFields": order_by,
        "resultOffset": offset,
        "resultRecordCount": _ARCGIS_PAGE_SIZE,
        "f": "json",
        "returnGeometry": "false",
    }
    response = await client.get(NBI_ARCGIS_URL, params=params, timeout=30.0)
    response.raise_for_status()
    return response.json()


async def get_bridge_condition(inp: BridgeConditionInput) -> list[dict[str, Any]] | dict[str, Any]:
    """Query the FHWA National Bridge Inventory for bridges matching the given criteria.

    Returns a list of normalised bridge dicts, or a structured error dict on failure.
    """
    tool_start = time.monotonic()
    retrieved_at = datetime.now(timezone.utc).isoformat()

    where = _build_where_clause(inp)
    results: list[dict[str, Any]] = []

    try:
        async with httpx.AsyncClient() as client:
            offset = 0
            while True:
                api_start = time.monotonic()
                try:
                    page = await _fetch_page(client, where, inp.order_by, offset)
                    api_latency_ms = (time.monotonic() - api_start) * 1000
                    emit_external_api("bts_arcgis", api_latency_ms)
                except httpx.HTTPStatusError as exc:
                    api_latency_ms = (time.monotonic() - api_start) * 1000
                    status_code = exc.response.status_code
                    retriable = status_code in {429, 500, 502, 503, 504}
                    emit_external_api(
                        "bts_arcgis",
                        api_latency_ms,
                        error_type=f"http_{status_code}",
                    )
                    tool_latency_ms = (time.monotonic() - tool_start) * 1000
                    emit_tool_call("get_bridge_condition", tool_latency_ms, "error")
                    return {
                        "error": f"BTS ArcGIS HTTP error {status_code}: {exc.response.text[:200]}",
                        "source": "bts_arcgis",
                        "retriable": retriable,
                    }
                except httpx.RequestError as exc:
                    api_latency_ms = (time.monotonic() - api_start) * 1000
                    emit_external_api("bts_arcgis", api_latency_ms, error_type="request_error")
                    tool_latency_ms = (time.monotonic() - tool_start) * 1000
                    emit_tool_call("get_bridge_condition", tool_latency_ms, "error")
                    return {
                        "error": f"BTS ArcGIS request error: {exc}",
                        "source": "bts_arcgis",
                        "retriable": True,
                    }

                # Surface-level ArcGIS error (returned as JSON with "error" key)
                if "error" in page:
                    arc_err = page["error"]
                    emit_external_api(
                        "bts_arcgis",
                        (time.monotonic() - api_start) * 1000,
                        error_type="arcgis_error",
                    )
                    tool_latency_ms = (time.monotonic() - tool_start) * 1000
                    emit_tool_call("get_bridge_condition", tool_latency_ms, "error")
                    return {
                        "error": f"ArcGIS error {arc_err.get('code')}: {arc_err.get('message')}",
                        "source": "bts_arcgis",
                        "retriable": False,
                    }

                features: list[dict] = page.get("features", [])
                for feat in features:
                    bridge = _normalise_feature(feat.get("attributes", {}), retrieved_at)
                    results.append(bridge)
                    if len(results) >= inp.limit:
                        break

                # Stop pagination when we have enough records or the page is not full
                if len(results) >= inp.limit or len(features) < _ARCGIS_PAGE_SIZE:
                    break

                offset += _ARCGIS_PAGE_SIZE

    except Exception as exc:
        tool_latency_ms = (time.monotonic() - tool_start) * 1000
        emit_tool_call("get_bridge_condition", tool_latency_ms, "error")
        logger.exception("Unexpected error in get_bridge_condition")
        return {
            "error": f"Unexpected error: {exc}",
            "source": "bts_arcgis",
            "retriable": False,
        }

    # Trim to requested limit (pagination loop may overshoot on boundary)
    results = results[: inp.limit]

    tool_latency_ms = (time.monotonic() - tool_start) * 1000
    emit_tool_call(
        "get_bridge_condition",
        tool_latency_ms,
        "success",
        result_count=len(results),
    )

    return results
