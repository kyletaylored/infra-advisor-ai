import ddtrace.auto  # must be first import — enables APM auto-instrumentation

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, List, Optional

import httpx
from pydantic import BaseModel

from observability.metrics import emit_external_api, emit_tool_call

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EIA_API_URL = "https://api.eia.gov/v2/electricity/electric-power-operational-data/data/"

VALID_DATA_SERIES = {"generation", "capacity", "fuel_mix"}

# EIA column name mapped by data_series selection
DATA_SERIES_COLUMN: dict[str, str] = {
    "generation": "generation",
    "capacity": "capacity",
    "fuel_mix": "generation",  # fuel_mix uses generation column filtered by fuel type
}

DATA_SERIES_UNITS: dict[str, str] = {
    "generation": "MWh",    # converted from thousand MWh at normalisation time
    "capacity": "MW",
    "fuel_mix": "MWh",
}


# ---------------------------------------------------------------------------
# Input schema
# ---------------------------------------------------------------------------


class EnergyInfrastructureInput(BaseModel):
    states: List[str]
    data_series: str = "generation"          # "generation" | "capacity" | "fuel_mix"
    year_from: Optional[int] = None
    year_to: Optional[int] = None
    fuel_types: Optional[List[str]] = None   # e.g. "SUN", "WND", "NG", "COL"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_params_list(input_data: EnergyInfrastructureInput) -> list:
    """
    Return EIA v2 query params as a list of (key, value) tuples so that
    httpx serialises repeated facets correctly:
        facets[location][]=TX&facets[location][]=FL …
    """
    api_key = os.environ["EIA_API_KEY"]
    data_col = DATA_SERIES_COLUMN.get(input_data.data_series, "generation")

    pairs: list = [
        ("api_key", api_key),
        ("frequency", "annual"),
        ("data[]", data_col),
        ("sort[0][column]", "period"),
        ("sort[0][direction]", "desc"),
        ("length", "5000"),
    ]

    for state in input_data.states:
        pairs.append(("facets[location][]", state))

    if input_data.fuel_types:
        for ft in input_data.fuel_types:
            pairs.append(("facets[fueltypeid][]", ft))

    if input_data.year_from:
        pairs.append(("start", str(input_data.year_from)))
    if input_data.year_to:
        pairs.append(("end", str(input_data.year_to)))

    return pairs


def _normalise_record(row: dict, data_series: str) -> dict[str, Any]:
    """
    Map a single EIA API response row to a standardised result dict.

    EIA v2 returns values in thousand MWh for generation; we convert to MWh.
    """
    period = row.get("period", "")
    state = row.get("location") or row.get("stateid", "")
    fuel_type = row.get("fueltypeid") or row.get("fuelTypeId", "")
    data_col = DATA_SERIES_COLUMN.get(data_series, "generation")
    raw_value = row.get(data_col)

    record: dict[str, Any] = {
        "state": state,
        "year": period[:4] if period else None,
        "fuel_type": fuel_type,
        "_source": "EIA",
        "_retrieved_at": datetime.now(timezone.utc).isoformat(),
    }

    if data_series in ("generation", "fuel_mix"):
        # EIA v2 reports generation in thousand MWh — multiply by 1000
        record["generation_mwh"] = float(raw_value) * 1000 if raw_value is not None else None
        record["units"] = "MWh"
    else:
        record["capacity_mw"] = float(raw_value) if raw_value is not None else None
        record["units"] = "MW"

    return record


# ---------------------------------------------------------------------------
# Public tool entry point
# ---------------------------------------------------------------------------


async def get_energy_infrastructure(
    input_data: EnergyInfrastructureInput,
) -> list[dict[str, Any]] | dict[str, Any]:
    """
    Query the EIA Open Data API (v2) for state-level energy generation or
    capacity data.

    Returns a list of normalised records on success, or a structured error
    dict on failure (never raises).
    """
    tool_start = time.monotonic()

    if input_data.data_series not in VALID_DATA_SERIES:
        tool_latency_ms = (time.monotonic() - tool_start) * 1000
        emit_tool_call("get_energy_infrastructure", tool_latency_ms, "error")
        return {
            "error": (
                f"Invalid data_series '{input_data.data_series}'. "
                f"Must be one of: {sorted(VALID_DATA_SERIES)}"
            ),
            "source": "eia",
            "retriable": False,
        }

    if not os.environ.get("EIA_API_KEY"):
        tool_latency_ms = (time.monotonic() - tool_start) * 1000
        emit_tool_call("get_energy_infrastructure", tool_latency_ms, "error")
        return {
            "error": "EIA_API_KEY environment variable is not set.",
            "source": "eia",
            "retriable": False,
        }

    params = _build_params_list(input_data)

    api_start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(EIA_API_URL, params=params)
            api_latency_ms = (time.monotonic() - api_start) * 1000

            if response.status_code == 429:
                emit_external_api("eia", api_latency_ms, error_type="rate_limit")
                emit_tool_call(
                    "get_energy_infrastructure",
                    (time.monotonic() - tool_start) * 1000,
                    "error",
                )
                return {"error": "EIA API rate limit exceeded.", "source": "eia", "retriable": True}

            if response.status_code >= 500:
                emit_external_api("eia", api_latency_ms, error_type=f"http_{response.status_code}")
                emit_tool_call(
                    "get_energy_infrastructure",
                    (time.monotonic() - tool_start) * 1000,
                    "error",
                )
                return {
                    "error": f"EIA API server error: HTTP {response.status_code}",
                    "source": "eia",
                    "retriable": True,
                }

            if response.status_code >= 400:
                emit_external_api("eia", api_latency_ms, error_type=f"http_{response.status_code}")
                emit_tool_call(
                    "get_energy_infrastructure",
                    (time.monotonic() - tool_start) * 1000,
                    "error",
                )
                return {
                    "error": (
                        f"EIA API client error: HTTP {response.status_code} — "
                        f"{response.text[:200]}"
                    ),
                    "source": "eia",
                    "retriable": False,
                }

            emit_external_api("eia", api_latency_ms)
            body = response.json()

    except httpx.TimeoutException:
        api_latency_ms = (time.monotonic() - api_start) * 1000
        emit_external_api("eia", api_latency_ms, error_type="timeout")
        emit_tool_call(
            "get_energy_infrastructure", (time.monotonic() - tool_start) * 1000, "error"
        )
        return {"error": "EIA API request timed out.", "source": "eia", "retriable": True}

    except httpx.RequestError as exc:
        api_latency_ms = (time.monotonic() - api_start) * 1000
        emit_external_api("eia", api_latency_ms, error_type="request_error")
        emit_tool_call(
            "get_energy_infrastructure", (time.monotonic() - tool_start) * 1000, "error"
        )
        return {
            "error": f"EIA API request failed: {exc}",
            "source": "eia",
            "retriable": True,
        }

    except Exception as exc:
        api_latency_ms = (time.monotonic() - api_start) * 1000
        emit_external_api("eia", api_latency_ms, error_type="unexpected")
        emit_tool_call(
            "get_energy_infrastructure", (time.monotonic() - tool_start) * 1000, "error"
        )
        logger.exception("Unexpected error in get_energy_infrastructure")
        return {
            "error": f"Unexpected error: {exc}",
            "source": "eia",
            "retriable": False,
        }

    # EIA v2 nests data inside response.response.data
    rows = body.get("response", {}).get("data", [])
    if not rows:
        logger.info("EIA returned zero rows for states=%s series=%s", input_data.states, input_data.data_series)
        tool_latency_ms = (time.monotonic() - tool_start) * 1000
        emit_tool_call("get_energy_infrastructure", tool_latency_ms, "success", result_count=0)
        return []

    results = [_normalise_record(row, input_data.data_series) for row in rows]

    tool_latency_ms = (time.monotonic() - tool_start) * 1000
    emit_tool_call(
        "get_energy_infrastructure", tool_latency_ms, "success", result_count=len(results)
    )
    logger.info("EIA returned %d records", len(results))
    return results
