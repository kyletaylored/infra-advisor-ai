import ddtrace.auto  # must be first import

import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from pydantic import BaseModel, Field

from observability.metrics import emit_external_api, emit_tool_call

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# OpenFEMA endpoint
# ---------------------------------------------------------------------------
OPENFEMA_URL = "https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries"

# OpenFEMA max records per page
_FEMA_PAGE_SIZE = 1000


# ---------------------------------------------------------------------------
# Input model
# ---------------------------------------------------------------------------
class DisasterHistoryInput(BaseModel):
    """Input parameters for the get_disaster_history tool."""

    states: Optional[list[str]] = Field(
        default=None,
        description="List of 2-letter state codes, e.g. ['TX', 'LA', 'MS']",
    )
    incident_types: Optional[list[str]] = Field(
        default=None,
        description="FEMA incident type names, e.g. ['Flood', 'Hurricane']",
    )
    date_from: Optional[str] = Field(
        default=None,
        description="ISO date string — return declarations on or after this date",
    )
    date_to: Optional[str] = Field(
        default=None,
        description="ISO date string — return declarations on or before this date",
    )
    infrastructure_keywords: Optional[list[str]] = Field(
        default=None,
        description=(
            "Client-side keyword filter applied to declarationTitle. "
            "A declaration is included if its title contains any of the supplied keywords "
            "(case-insensitive)."
        ),
    )
    limit: int = Field(
        default=100,
        ge=1,
        le=1000,
        description="Maximum number of declarations to return (1–1000)",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_odata_filter(inp: DisasterHistoryInput) -> str | None:
    """Build an OData $filter expression from the input model."""
    parts: list[str] = []

    if inp.states:
        # OData: state eq 'TX' or state eq 'LA'
        state_clauses = " or ".join(f"state eq '{s.upper()}'" for s in inp.states)
        if len(inp.states) > 1:
            parts.append(f"({state_clauses})")
        else:
            parts.append(state_clauses)

    if inp.incident_types:
        type_clauses = " or ".join(
            f"incidentType eq '{t}'" for t in inp.incident_types
        )
        if len(inp.incident_types) > 1:
            parts.append(f"({type_clauses})")
        else:
            parts.append(type_clauses)

    if inp.date_from:
        parts.append(f"declarationDate ge '{inp.date_from}T00:00:00.000z'")

    if inp.date_to:
        parts.append(f"declarationDate le '{inp.date_to}T23:59:59.999z'")

    return " and ".join(parts) if parts else None


def _matches_keywords(title: str | None, keywords: list[str]) -> bool:
    """Return True if the title contains any of the keywords (case-insensitive)."""
    if not title:
        return False
    title_lower = title.lower()
    return any(kw.lower() in title_lower for kw in keywords)


def _normalise_declaration(record: dict[str, Any], retrieved_at: str) -> dict[str, Any]:
    """Convert a raw OpenFEMA DisasterDeclarationsSummaries record to a normalised dict."""
    return {
        "disaster_number": record.get("disasterNumber"),
        "declaration_type": record.get("declarationType"),
        "declaration_title": record.get("declarationTitle"),
        "incident_type": record.get("incidentType"),
        "state": record.get("state"),
        "designated_area": record.get("designatedArea"),
        "declaration_date": record.get("declarationDate"),
        "incident_begin_date": record.get("incidentBeginDate"),
        "incident_end_date": record.get("incidentEndDate"),
        "close_out_date": record.get("closeOutDate"),
        "fips_state_code": record.get("fipsStateCode"),
        "fips_county_code": record.get("fipsCountyCode"),
        "program_declared": {
            "ih": record.get("ihProgramDeclared"),
            "ia": record.get("iaProgramDeclared"),
            "pa": record.get("paProgramDeclared"),
            "hm": record.get("hmProgramDeclared"),
        },
        "_source": "OpenFEMA",
        "_retrieved_at": retrieved_at,
    }


# ---------------------------------------------------------------------------
# Core async implementation
# ---------------------------------------------------------------------------

async def _fetch_page(
    client: httpx.AsyncClient,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Fetch a single page from the OpenFEMA API."""
    response = await client.get(OPENFEMA_URL, params=params, timeout=30.0)
    response.raise_for_status()
    return response.json()


async def get_disaster_history(
    inp: DisasterHistoryInput,
) -> list[dict[str, Any]] | dict[str, Any]:
    """Query OpenFEMA for disaster declarations matching the given criteria.

    Returns a list of normalised declaration dicts, or a structured error dict on failure.
    """
    tool_start = time.monotonic()
    retrieved_at = datetime.now(timezone.utc).isoformat()

    odata_filter = _build_odata_filter(inp)
    results: list[dict[str, Any]] = []

    # Base query parameters
    base_params: dict[str, Any] = {
        "$format": "json",
        "$orderby": "declarationDate desc",
        "$top": min(inp.limit, _FEMA_PAGE_SIZE),
    }
    if odata_filter:
        base_params["$filter"] = odata_filter

    try:
        async with httpx.AsyncClient() as client:
            skip = 0
            while True:
                page_params = dict(base_params)
                if skip > 0:
                    page_params["$skip"] = skip

                api_start = time.monotonic()
                try:
                    page = await _fetch_page(client, page_params)
                    api_latency_ms = (time.monotonic() - api_start) * 1000
                    emit_external_api("openfema", api_latency_ms)
                except httpx.HTTPStatusError as exc:
                    api_latency_ms = (time.monotonic() - api_start) * 1000
                    status_code = exc.response.status_code
                    retriable = status_code in {429, 500, 502, 503, 504}
                    emit_external_api(
                        "openfema",
                        api_latency_ms,
                        error_type=f"http_{status_code}",
                    )
                    tool_latency_ms = (time.monotonic() - tool_start) * 1000
                    emit_tool_call("get_disaster_history", tool_latency_ms, "error")
                    return {
                        "error": f"OpenFEMA HTTP error {status_code}: {exc.response.text[:200]}",
                        "source": "openfema",
                        "retriable": retriable,
                    }
                except httpx.RequestError as exc:
                    api_latency_ms = (time.monotonic() - api_start) * 1000
                    emit_external_api("openfema", api_latency_ms, error_type="request_error")
                    tool_latency_ms = (time.monotonic() - tool_start) * 1000
                    emit_tool_call("get_disaster_history", tool_latency_ms, "error")
                    return {
                        "error": f"OpenFEMA request error: {exc}",
                        "source": "openfema",
                        "retriable": True,
                    }

                # OpenFEMA wraps records in a top-level key matching the endpoint name
                records: list[dict] = page.get("DisasterDeclarationsSummaries", [])

                for rec in records:
                    # Apply client-side infrastructure keyword filter when requested
                    if inp.infrastructure_keywords:
                        if not _matches_keywords(
                            rec.get("declarationTitle"), inp.infrastructure_keywords
                        ):
                            continue

                    results.append(_normalise_declaration(rec, retrieved_at))

                    if len(results) >= inp.limit:
                        break

                # Stop pagination when we have enough or the page is not full
                if len(results) >= inp.limit or len(records) < _FEMA_PAGE_SIZE:
                    break

                skip += _FEMA_PAGE_SIZE

    except Exception as exc:
        tool_latency_ms = (time.monotonic() - tool_start) * 1000
        emit_tool_call("get_disaster_history", tool_latency_ms, "error")
        logger.exception("Unexpected error in get_disaster_history")
        return {
            "error": f"Unexpected error: {exc}",
            "source": "openfema",
            "retriable": False,
        }

    tool_latency_ms = (time.monotonic() - tool_start) * 1000
    emit_tool_call(
        "get_disaster_history",
        tool_latency_ms,
        "success",
        result_count=len(results),
    )

    return results
