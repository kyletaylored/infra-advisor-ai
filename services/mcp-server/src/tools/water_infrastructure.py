import ddtrace.auto  # must be first import — enables APM auto-instrumentation

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, List, Literal, Optional

import httpx
from pydantic import BaseModel

from observability.metrics import emit_external_api, emit_tool_call

try:
    from azure.core.credentials import AzureKeyCredential  # type: ignore
    from azure.search.documents import SearchClient  # type: ignore
except ImportError:  # pragma: no cover
    AzureKeyCredential = None  # type: ignore
    SearchClient = None  # type: ignore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EPA_SDWIS_DEFAULT_BASE = "https://enviro.epa.gov/enviro/efservice"

TWDB_DOMAIN = "water"
TWDB_DOC_TYPE = "water_plan_project"
TWDB_SOURCE_LABEL = "TWDB_2026_State_Water_Plan"
EPA_SOURCE_LABEL = "EPA_SDWIS"


# ---------------------------------------------------------------------------
# Input schema
# ---------------------------------------------------------------------------


class WaterInfrastructureInput(BaseModel):
    query_type: Literal["water_systems", "water_plan_projects", "violations"]
    states: Optional[List[str]] = None
    counties: Optional[List[str]] = None
    planning_regions: Optional[List[str]] = None   # TWDB region codes A–P
    project_types: Optional[List[str]] = None      # "desalination", "aquifer_storage", etc.
    system_types: Optional[List[str]] = None       # "CWS", "NTNCWS", "TNCWS"
    has_violations: Optional[bool] = None
    min_population_served: Optional[int] = None
    limit: int = 50


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _epa_base_url() -> str:
    return os.environ.get("EPA_SDWIS_BASE_URL", EPA_SDWIS_DEFAULT_BASE).rstrip("/")


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalise_water_system(rec: dict) -> dict[str, Any]:
    """Map an EPA SDWIS WATER_SYSTEM JSON record to the standard output schema."""
    return {
        "system_name": rec.get("PWS_NAME") or rec.get("pws_name", ""),
        "pwsid": rec.get("PWSID") or rec.get("pwsid", ""),
        "city": rec.get("CITY_NAME") or rec.get("city_name", ""),
        "county": rec.get("COUNTY_SERVED") or rec.get("county_served", ""),
        "state": rec.get("STATE_CODE") or rec.get("state_code", ""),
        "population_served": _safe_int(
            rec.get("POPULATION_SERVED_COUNT") or rec.get("population_served_count")
        ),
        "primary_source_type": rec.get("PRIMARY_SOURCE_CODE") or rec.get("primary_source_code", ""),
        "pws_type": rec.get("PWS_TYPE_CODE") or rec.get("pws_type_code", ""),
        "open_violation_count": None,  # populated by _attach_violation_counts if needed
        "last_inspection_date": (
            rec.get("LAST_INSPECTION_DATE") or rec.get("last_inspection_date")
        ),
        "_source": EPA_SOURCE_LABEL,
        "_retrieved_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# EPA SDWIS sub-queries
# ---------------------------------------------------------------------------


async def _fetch_epa_water_systems(
    states: List[str],
    system_types: Optional[List[str]],
    has_violations: Optional[bool],
    min_population_served: Optional[int],
    limit: int,
) -> list[dict[str, Any]]:
    """
    Query EPA Envirofacts SDWIS for public water systems.

    Endpoint:
        GET {EPA_SDWIS_BASE_URL}/WATER_SYSTEM/STATE_CODE/{state}/PWS_TYPE_CODE/{type}/JSON
    """
    base = _epa_base_url()
    pws_types = system_types if system_types else ["CWS"]
    results: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=30.0) as client:
        for state in states:
            for pws_type in pws_types:
                url = f"{base}/WATER_SYSTEM/STATE_CODE/{state}/PWS_TYPE_CODE/{pws_type}/JSON"
                api_start = time.monotonic()
                try:
                    resp = await client.get(url)
                    api_latency_ms = (time.monotonic() - api_start) * 1000

                    if resp.status_code == 404:
                        emit_external_api("epa_sdwis", api_latency_ms)
                        logger.warning(
                            "EPA SDWIS 404 for state=%s pws_type=%s", state, pws_type
                        )
                        continue

                    resp.raise_for_status()
                    emit_external_api("epa_sdwis", api_latency_ms)

                    raw = resp.json()
                    records: list = raw if isinstance(raw, list) else raw.get("results", [])

                    for rec in records:
                        normalised = _normalise_water_system(rec)

                        if min_population_served is not None:
                            pop = normalised.get("population_served")
                            if pop is None or pop < min_population_served:
                                continue

                        results.append(normalised)

                except httpx.TimeoutException:
                    api_latency_ms = (time.monotonic() - api_start) * 1000
                    emit_external_api("epa_sdwis", api_latency_ms, error_type="timeout")
                    logger.error("Timeout querying EPA SDWIS for state=%s", state)
                except httpx.HTTPStatusError as exc:
                    api_latency_ms = (time.monotonic() - api_start) * 1000
                    emit_external_api(
                        "epa_sdwis", api_latency_ms,
                        error_type=f"http_{exc.response.status_code}",
                    )
                    logger.error("HTTP error from EPA SDWIS: %s", exc)
                except Exception as exc:
                    api_latency_ms = (time.monotonic() - api_start) * 1000
                    emit_external_api("epa_sdwis", api_latency_ms, error_type="unexpected")
                    logger.error("Unexpected error from EPA SDWIS: %s", exc)

    # Attach violation counts if caller wants violation filtering
    if has_violations is not None:
        results = await _attach_violation_counts(results)
        if has_violations:
            results = [r for r in results if (r.get("open_violation_count") or 0) > 0]
        else:
            results = [r for r in results if (r.get("open_violation_count") or 0) == 0]

    return results[:limit]


async def _attach_violation_counts(systems: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Fetch open violation counts from the SDWA_VIOLATIONS endpoint and attach
    them to each water system record in-place.
    """
    base = _epa_base_url()

    async with httpx.AsyncClient(timeout=30.0) as client:
        for system in systems:
            pwsid = system.get("pwsid")
            if not pwsid:
                system["open_violation_count"] = 0
                continue

            url = f"{base}/SDWA_VIOLATIONS/PWSID/{pwsid}/IS_HEALTH_BASED_IND/Y/JSON"
            api_start = time.monotonic()
            try:
                resp = await client.get(url)
                api_latency_ms = (time.monotonic() - api_start) * 1000

                if resp.status_code == 404:
                    emit_external_api("epa_sdwis", api_latency_ms)
                    system["open_violation_count"] = 0
                    continue

                resp.raise_for_status()
                emit_external_api("epa_sdwis", api_latency_ms)

                violations: list = resp.json()
                if not isinstance(violations, list):
                    violations = []

                open_count = sum(
                    1
                    for v in violations
                    if str(
                        v.get("VIOLATION_STATUS") or v.get("violation_status", "")
                    ).upper() in ("OPEN", "UNRESOLVED", "")
                )
                system["open_violation_count"] = open_count

            except Exception:
                api_latency_ms = (time.monotonic() - api_start) * 1000
                emit_external_api("epa_sdwis", api_latency_ms, error_type="violation_fetch_error")
                system["open_violation_count"] = None

    return systems


# ---------------------------------------------------------------------------
# TWDB Azure AI Search sub-query
# ---------------------------------------------------------------------------


def _build_twdb_filter(
    counties: Optional[List[str]],
    planning_regions: Optional[List[str]],
) -> str:
    """Build an OData $filter string scoped to TWDB water plan documents."""
    clauses: list[str] = [
        f"domain eq '{TWDB_DOMAIN}'",
        f"document_type eq '{TWDB_DOC_TYPE}'",
    ]
    return " and ".join(clauses)


def _parse_twdb_chunk(result: Any) -> dict[str, Any]:
    """
    Extract water plan fields from an Azure AI Search result.

    Structural metadata fields (project_name, county, etc.) are surfaced when
    present.  The full narrative is always returned in 'content'.
    """
    score = getattr(result, "@search.score", None) or getattr(result, "score", None)

    record: dict[str, Any] = {
        "content": getattr(result, "content", "") or "",
        "source": getattr(result, "source", TWDB_SOURCE_LABEL) or TWDB_SOURCE_LABEL,
        "document_type": TWDB_DOC_TYPE,
        "domain": TWDB_DOMAIN,
        "score": float(score) if score is not None else None,
        "source_url": getattr(result, "source_url", None),
        "_source": TWDB_SOURCE_LABEL,
        "_retrieved_at": datetime.now(timezone.utc).isoformat(),
    }

    # Best-effort extraction of structured fields
    for field in (
        "project_name",
        "county",
        "planning_region",
        "strategy_type",
        "estimated_cost",
        "decade_of_need",
        "water_user_group",
    ):
        val = getattr(result, field, None)
        if val is not None:
            record[field] = val

    return record


async def _fetch_twdb_projects(
    counties: Optional[List[str]],
    planning_regions: Optional[List[str]],
    project_types: Optional[List[str]],
    limit: int,
) -> list[dict[str, Any]]:
    """Query Azure AI Search for TWDB 2026 State Water Plan project records."""
    endpoint = os.environ.get("AZURE_SEARCH_ENDPOINT", "")
    api_key = os.environ.get("AZURE_SEARCH_API_KEY", "")
    index_name = os.environ.get("AZURE_SEARCH_INDEX_NAME", "infra-advisor-knowledge")

    if not endpoint or not api_key:
        logger.error("Azure AI Search credentials not configured for TWDB query")
        return [
            {
                "_source": TWDB_SOURCE_LABEL,
                "error": "Azure AI Search endpoint or API key not configured.",
                "retriable": False,
                "content": "",
            }
        ]

    if SearchClient is None or AzureKeyCredential is None:
        logger.error("azure-search-documents not installed")
        return [
            {
                "_source": TWDB_SOURCE_LABEL,
                "error": "azure-search-documents package not available.",
                "retriable": False,
                "content": "",
            }
        ]

    search_client = SearchClient(
        endpoint=endpoint,
        index_name=index_name,
        credential=AzureKeyCredential(api_key),
    )

    # Build search text from optional filters
    search_parts: list[str] = ["water plan project"]
    if planning_regions:
        search_parts.extend([f"region {r}" for r in planning_regions])
    if counties:
        search_parts.extend(counties)
    if project_types:
        search_parts.extend(project_types)
    search_text = " ".join(search_parts)

    odata_filter = _build_twdb_filter(counties, planning_regions)

    api_start = time.monotonic()
    try:
        results_iter = search_client.search(
            search_text=search_text,
            filter=odata_filter,
            top=limit,
            include_total_count=True,
        )
        records = [_parse_twdb_chunk(r) for r in results_iter]
        api_latency_ms = (time.monotonic() - api_start) * 1000
        emit_external_api("twdb", api_latency_ms)
        logger.info("TWDB AI Search returned %d records", len(records))
        return records

    except Exception as exc:
        api_latency_ms = (time.monotonic() - api_start) * 1000
        emit_external_api("twdb", api_latency_ms, error_type="search_error")
        logger.error("Azure AI Search error for TWDB query: %s", exc)
        return [
            {
                "_source": TWDB_SOURCE_LABEL,
                "error": f"Azure AI Search query failed: {exc}",
                "retriable": True,
                "content": "",
            }
        ]


# ---------------------------------------------------------------------------
# Public tool entry point
# ---------------------------------------------------------------------------


async def get_water_infrastructure(
    input_data: WaterInfrastructureInput,
) -> list[dict[str, Any]] | dict[str, Any]:
    """
    Query water infrastructure data from EPA SDWIS (for water_systems /
    violations) or the TWDB 2026 State Water Plan AI Search index (for
    water_plan_projects).

    CRITICAL: every result dict includes _source set to either
    "EPA_SDWIS" or "TWDB_2026_State_Water_Plan".
    """
    tool_start = time.monotonic()

    if input_data.query_type in ("water_systems", "violations"):
        states = input_data.states or []
        if not states:
            emit_tool_call(
                "get_water_infrastructure",
                (time.monotonic() - tool_start) * 1000,
                "error",
            )
            return {
                "error": "At least one state must be provided for water_systems/violations queries.",
                "source": "epa_sdwis",
                "retriable": False,
            }

        results = await _fetch_epa_water_systems(
            states=states,
            system_types=input_data.system_types,
            has_violations=input_data.has_violations,
            min_population_served=input_data.min_population_served,
            limit=input_data.limit,
        )

        # Guarantee _source on every result
        for r in results:
            r.setdefault("_source", EPA_SOURCE_LABEL)

        emit_tool_call(
            "get_water_infrastructure",
            (time.monotonic() - tool_start) * 1000,
            "success",
            result_count=len(results),
        )
        return results

    elif input_data.query_type == "water_plan_projects":
        results = await _fetch_twdb_projects(
            counties=input_data.counties,
            planning_regions=input_data.planning_regions,
            project_types=input_data.project_types,
            limit=input_data.limit,
        )

        # Guarantee _source on every result
        for r in results:
            r.setdefault("_source", TWDB_SOURCE_LABEL)

        emit_tool_call(
            "get_water_infrastructure",
            (time.monotonic() - tool_start) * 1000,
            "success",
            result_count=len(results),
        )
        return results

    else:
        emit_tool_call(
            "get_water_infrastructure",
            (time.monotonic() - tool_start) * 1000,
            "error",
        )
        return {
            "error": f"Unknown query_type: {input_data.query_type!r}",
            "source": "water_infrastructure",
            "retriable": False,
        }
