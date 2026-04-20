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

ERCOT_BASE_URL = os.environ.get("ERCOT_API_BASE_URL", "https://api.ercot.com/api/public-data")

# Product ID for Energy Storage Resource 4-second charging data
ESR_CHARGING_PRODUCT = "rptesr-m/4_sec_esr_charging_mw"


# ---------------------------------------------------------------------------
# Input schema
# ---------------------------------------------------------------------------


class ERCOTEnergyStorageInput(BaseModel):
    query_type: Literal["charging_data", "products"] = "charging_data"
    time_from: Optional[str] = None   # ISO-8601 e.g. "2024-06-01T00:00:00"
    time_to: Optional[str] = None     # ISO-8601 e.g. "2024-06-01T01:00:00"
    min_charging_mw: Optional[float] = None
    max_charging_mw: Optional[float] = None
    page: int = 1
    size: int = 100


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _api_key() -> str:
    key = os.environ.get("ERCOT_API_KEY", "")
    if not key:
        raise EnvironmentError("ERCOT_API_KEY environment variable is not set.")
    return key


def _headers() -> dict:
    return {
        "Ocp-Apim-Subscription-Key": _api_key(),
        "Accept": "application/json",
    }


def _normalise_charging_record(row: dict) -> dict[str, Any]:
    return {
        "agc_exec_time": row.get("AGCExecTime") or row.get("agcExecTime"),
        "agc_exec_time_utc": row.get("AGCExecTimeUTC") or row.get("agcExecTimeUTC"),
        "system_demand_mw": row.get("systemDemand"),
        "esr_charging_mw": row.get("ESRChargingMW") or row.get("esrChargingMW"),
        "_source": "ERCOT_ESR",
        "_retrieved_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Public tool entry point
# ---------------------------------------------------------------------------


async def get_ercot_energy_storage(
    input_data: ERCOTEnergyStorageInput,
) -> list[dict[str, Any]] | dict[str, Any]:
    """
    Query ERCOT's public data API for Energy Storage Resource (ESR) data.

    Returns normalised records on success or a structured error dict (never raises).
    """
    tool_start = time.monotonic()

    try:
        api_key_val = _api_key()
    except EnvironmentError as exc:
        emit_tool_call("get_ercot_energy_storage", 0, "error")
        return {"error": str(exc), "source": "ercot", "retriable": False}

    if input_data.query_type == "products":
        return await _list_products(api_key_val, tool_start)

    return await _query_charging_data(input_data, api_key_val, tool_start)


async def _list_products(api_key_val: str, tool_start: float) -> list | dict:
    """List available ERCOT public data products."""
    headers = {"Ocp-Apim-Subscription-Key": api_key_val, "Accept": "application/json"}
    url = ERCOT_BASE_URL + "/"

    api_start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url, headers=headers)
            api_latency_ms = (time.monotonic() - api_start) * 1000

            if resp.status_code >= 400:
                emit_external_api("ercot", api_latency_ms, error_type=f"http_{resp.status_code}")
                emit_tool_call("get_ercot_energy_storage", (time.monotonic() - tool_start) * 1000, "error")
                return {"error": f"ERCOT API error: HTTP {resp.status_code}", "source": "ercot", "retriable": resp.status_code >= 500}

            emit_external_api("ercot", api_latency_ms)
            body = resp.json()

    except httpx.TimeoutException:
        api_latency_ms = (time.monotonic() - api_start) * 1000
        emit_external_api("ercot", api_latency_ms, error_type="timeout")
        emit_tool_call("get_ercot_energy_storage", (time.monotonic() - tool_start) * 1000, "error")
        return {"error": "ERCOT API request timed out.", "source": "ercot", "retriable": True}

    except httpx.RequestError as exc:
        api_latency_ms = (time.monotonic() - api_start) * 1000
        emit_external_api("ercot", api_latency_ms, error_type="request_error")
        emit_tool_call("get_ercot_energy_storage", (time.monotonic() - tool_start) * 1000, "error")
        return {"error": f"ERCOT API request failed: {exc}", "source": "ercot", "retriable": True}

    products = body if isinstance(body, list) else body.get("data", body.get("products", []))
    emit_tool_call("get_ercot_energy_storage", (time.monotonic() - tool_start) * 1000, "success", result_count=len(products))
    return [{"product_id": p.get("id") or p.get("productId"), "name": p.get("name"), "_source": "ERCOT_ESR"} for p in products] if isinstance(products, list) else [{"raw": products, "_source": "ERCOT_ESR"}]


async def _query_charging_data(
    input_data: ERCOTEnergyStorageInput,
    api_key_val: str,
    tool_start: float,
) -> list | dict:
    """Query the 4-second ESR charging MW endpoint."""
    url = f"{ERCOT_BASE_URL}/{ESR_CHARGING_PRODUCT}"
    headers = {"Ocp-Apim-Subscription-Key": api_key_val, "Accept": "application/json"}

    params: list[tuple[str, Any]] = [
        ("page", str(input_data.page)),
        ("size", str(input_data.size)),
        ("sort", "AGCExecTimeUTC"),
        ("dir", "desc"),
    ]
    if input_data.time_from:
        params.append(("AGCExecTimeFrom", input_data.time_from))
    if input_data.time_to:
        params.append(("AGCExecTimeTo", input_data.time_to))
    if input_data.min_charging_mw is not None:
        params.append(("ESRChargingMWFrom", str(input_data.min_charging_mw)))
    if input_data.max_charging_mw is not None:
        params.append(("ESRChargingMWTo", str(input_data.max_charging_mw)))

    api_start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=headers, params=params)
            api_latency_ms = (time.monotonic() - api_start) * 1000

            if resp.status_code == 429:
                emit_external_api("ercot", api_latency_ms, error_type="rate_limit")
                emit_tool_call("get_ercot_energy_storage", (time.monotonic() - tool_start) * 1000, "error")
                return {"error": "ERCOT API rate limit exceeded.", "source": "ercot", "retriable": True}

            if resp.status_code >= 400:
                emit_external_api("ercot", api_latency_ms, error_type=f"http_{resp.status_code}")
                emit_tool_call("get_ercot_energy_storage", (time.monotonic() - tool_start) * 1000, "error")
                return {
                    "error": f"ERCOT API error: HTTP {resp.status_code} — {resp.text[:200]}",
                    "source": "ercot",
                    "retriable": resp.status_code >= 500,
                }

            emit_external_api("ercot", api_latency_ms)
            body = resp.json()

    except httpx.TimeoutException:
        api_latency_ms = (time.monotonic() - api_start) * 1000
        emit_external_api("ercot", api_latency_ms, error_type="timeout")
        emit_tool_call("get_ercot_energy_storage", (time.monotonic() - tool_start) * 1000, "error")
        return {"error": "ERCOT API request timed out.", "source": "ercot", "retriable": True}

    except httpx.RequestError as exc:
        api_latency_ms = (time.monotonic() - api_start) * 1000
        emit_external_api("ercot", api_latency_ms, error_type="request_error")
        emit_tool_call("get_ercot_energy_storage", (time.monotonic() - tool_start) * 1000, "error")
        return {"error": f"ERCOT API request failed: {exc}", "source": "ercot", "retriable": True}

    except Exception:
        api_latency_ms = (time.monotonic() - api_start) * 1000
        emit_external_api("ercot", api_latency_ms, error_type="unexpected")
        emit_tool_call("get_ercot_energy_storage", (time.monotonic() - tool_start) * 1000, "error")
        logger.exception("Unexpected error in get_ercot_energy_storage")
        return {"error": "Unexpected error querying ERCOT API.", "source": "ercot", "retriable": False}

    # Response shape varies by ERCOT API version — handle both list and wrapped
    rows: list = []
    if isinstance(body, list):
        rows = body
    elif isinstance(body, dict):
        rows = body.get("data", body.get("rows", body.get("results", [])))

    if not rows:
        logger.info("ERCOT ESR returned zero rows for params=%s", params)
        emit_tool_call("get_ercot_energy_storage", (time.monotonic() - tool_start) * 1000, "success", result_count=0)
        return []

    results = [_normalise_charging_record(row) for row in rows]
    emit_tool_call("get_ercot_energy_storage", (time.monotonic() - tool_start) * 1000, "success", result_count=len(results))
    logger.info("ERCOT ESR returned %d records", len(results))
    return results
