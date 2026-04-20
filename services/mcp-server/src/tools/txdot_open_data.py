import ddtrace.auto  # must be first import — enables APM auto-instrumentation

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, List, Literal, Optional

import httpx
from pydantic import BaseModel

from observability.metrics import emit_external_api, emit_tool_call

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TXDOT_HUB_URL = os.environ.get(
    "TXDOT_HUB_URL", "https://gis-txdot.opendata.arcgis.com/api/search"
)

# Pre-built search terms for common infrastructure query types
_PRESET_QUERIES: dict[str, str] = {
    "catalog_search": "",           # caller provides `query` directly
    "traffic_counts": "AADT annual average daily traffic volume county",
    "construction_projects": "highway construction maintenance project lettings TxDOT",
}


# ---------------------------------------------------------------------------
# Input schema
# ---------------------------------------------------------------------------


class TxDOTOpenDataInput(BaseModel):
    query_type: Literal["catalog_search", "traffic_counts", "construction_projects"] = "catalog_search"
    query: str = ""          # free-text search; required for catalog_search
    county: Optional[str] = None   # Texas county name to filter results
    limit: int = 20
    page: int = 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalise_item(item: dict) -> dict[str, Any]:
    """Map a Hub search result item to a consistent record."""
    attributes = item.get("attributes", item)  # Hub v2 wraps in "attributes"
    url = attributes.get("url") or attributes.get("landingPage") or attributes.get("source", {}).get("url", "")
    return {
        "id": attributes.get("id") or attributes.get("itemId", ""),
        "title": attributes.get("title") or attributes.get("name", ""),
        "description": (attributes.get("description") or attributes.get("snippet", ""))[:400],
        "type": attributes.get("type") or attributes.get("itemType", ""),
        "url": url,
        "tags": attributes.get("tags", []),
        "access": attributes.get("access", "public"),
        "_source": "TxDOT_Open_Data",
        "_retrieved_at": datetime.now(timezone.utc).isoformat(),
    }


def _build_search_query(input_data: TxDOTOpenDataInput) -> str:
    """Combine preset + caller query + optional county filter."""
    parts: list[str] = []

    preset = _PRESET_QUERIES.get(input_data.query_type, "")
    if preset:
        parts.append(preset)

    if input_data.query:
        parts.append(input_data.query)

    if input_data.county:
        parts.append(f"{input_data.county} county")

    return " ".join(parts).strip() or "Texas infrastructure"


# ---------------------------------------------------------------------------
# Public tool entry point
# ---------------------------------------------------------------------------


async def search_txdot_open_data(
    input_data: TxDOTOpenDataInput,
) -> list[dict[str, Any]] | dict[str, Any]:
    """
    Search the TxDOT Open Data portal (ArcGIS Hub) for Texas transportation datasets.

    Returns a list of normalised dataset records on success, or a structured error dict.
    Never raises.
    """
    tool_start = time.monotonic()

    q = _build_search_query(input_data)
    params: dict[str, Any] = {
        "q": q,
        "collection": "Dataset",
        "num": min(input_data.limit, 50),
        "start": (input_data.page - 1) * input_data.limit + 1,
        "sortBy": "relevance",
    }

    api_start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(TXDOT_HUB_URL, params=params)
            api_latency_ms = (time.monotonic() - api_start) * 1000

            if resp.status_code >= 400:
                emit_external_api("txdot", api_latency_ms, error_type=f"http_{resp.status_code}")
                emit_tool_call("search_txdot_open_data", (time.monotonic() - tool_start) * 1000, "error")
                return {
                    "error": f"TxDOT Hub API error: HTTP {resp.status_code}",
                    "source": "txdot",
                    "retriable": resp.status_code >= 500,
                }

            emit_external_api("txdot", api_latency_ms)
            body = resp.json()

    except httpx.TimeoutException:
        api_latency_ms = (time.monotonic() - api_start) * 1000
        emit_external_api("txdot", api_latency_ms, error_type="timeout")
        emit_tool_call("search_txdot_open_data", (time.monotonic() - tool_start) * 1000, "error")
        return {"error": "TxDOT Hub API request timed out.", "source": "txdot", "retriable": True}

    except httpx.RequestError as exc:
        api_latency_ms = (time.monotonic() - api_start) * 1000
        emit_external_api("txdot", api_latency_ms, error_type="request_error")
        emit_tool_call("search_txdot_open_data", (time.monotonic() - tool_start) * 1000, "error")
        return {"error": f"TxDOT Hub API request failed: {exc}", "source": "txdot", "retriable": True}

    except Exception:
        api_latency_ms = (time.monotonic() - api_start) * 1000
        emit_external_api("txdot", api_latency_ms, error_type="unexpected")
        emit_tool_call("search_txdot_open_data", (time.monotonic() - tool_start) * 1000, "error")
        logger.exception("Unexpected error in search_txdot_open_data")
        return {"error": "Unexpected error querying TxDOT Open Data.", "source": "txdot", "retriable": False}

    # Hub API response shape varies by version — handle both list and wrapped
    items: list = []
    if isinstance(body, list):
        items = body
    elif isinstance(body, dict):
        items = (
            body.get("results")
            or body.get("data")
            or body.get("items")
            or []
        )

    if not items:
        logger.info("TxDOT Hub returned zero results for q=%r", q)
        emit_tool_call("search_txdot_open_data", (time.monotonic() - tool_start) * 1000, "success", result_count=0)
        return []

    results = [_normalise_item(item) for item in items]
    emit_tool_call(
        "search_txdot_open_data", (time.monotonic() - tool_start) * 1000, "success", result_count=len(results)
    )
    logger.info("TxDOT Hub returned %d results for q=%r", len(results), q)
    return results
