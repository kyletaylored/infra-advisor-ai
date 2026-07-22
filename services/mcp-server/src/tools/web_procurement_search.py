import ddtrace.auto  # must be first import — enables APM auto-instrumentation

import json
import logging
import os
import time
from typing import Optional

import httpx
from pydantic import BaseModel

from observability.metrics import emit_external_api, emit_tool_call
from observability.tracing import log_external_api_failure

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
#
# This tool calls Azure OpenAI's Responses API with the web_search_preview
# tool in a single round trip:
#
#   POST {AZURE_OPENAI_ENDPOINT}/openai/v1/responses?api-version=preview
#
# That endpoint runs the live web search server-side and emits structured
# results matching a JSON schema. Keeps the whole AI stack inside Azure
# (one vendor key, one usage meter) and halves both the latency and the
# external dependency surface.

_SECTOR_TERMS = {
    "transportation": "transportation infrastructure",
    "water": "water treatment infrastructure",
    "energy": "energy power infrastructure",
    "buildings": "commercial building construction",
    "environmental": "environmental remediation",
}


# ---------------------------------------------------------------------------
# Input schema
# ---------------------------------------------------------------------------


class WebProcurementSearchInput(BaseModel):
    query: str
    geography: Optional[str] = None
    sector: Optional[str] = None      # "transportation", "water", "energy", "buildings", "environmental"
    result_type: Optional[str] = None  # "rfp", "bond", "budget", "award", "any"
    limit: int = 8


# ---------------------------------------------------------------------------
# JSON-schema for structured results
# ---------------------------------------------------------------------------
#
# The Responses API constrains the model to this shape via
# text.format.json_schema. strict=true means the model cannot return
# fields outside the schema — keeps downstream parsing trivial.

_RESULTS_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["results"],
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "agency_name", "project_title", "project_description",
                    "estimated_value_usd", "deadline", "contact_email",
                    "source_url", "result_type", "confidence",
                ],
                "properties": {
                    "agency_name": {"type": ["string", "null"]},
                    "project_title": {"type": ["string", "null"]},
                    "project_description": {"type": ["string", "null"], "maxLength": 240},
                    "estimated_value_usd": {"type": ["integer", "null"]},
                    "deadline": {
                        "type": ["string", "null"],
                        "description": "ISO 8601 date (YYYY-MM-DD) or null when unknown",
                    },
                    "contact_email": {"type": ["string", "null"]},
                    "source_url": {"type": "string"},
                    "result_type": {"enum": ["rfp", "award", "bond", "budget", "other"]},
                    "confidence": {"enum": ["high", "medium", "low"]},
                },
            },
        },
    },
}


def _build_instructions(input_data: WebProcurementSearchInput) -> str:
    qualifiers = [input_data.query]
    if input_data.geography:
        qualifiers.append(f"in {input_data.geography}")
    if input_data.sector and input_data.sector in _SECTOR_TERMS:
        qualifiers.append(_SECTOR_TERMS[input_data.sector])

    type_phrase = {
        "rfp": "active requests for proposals (RFPs) and solicitations",
        "bond": "bond elections and municipal bond initiatives",
        "budget": "infrastructure budget and capital improvement plans",
        "award": "recent procurement awards",
    }.get(input_data.result_type or "", "procurement opportunities and announcements")

    return (
        f"Search the web for up to {input_data.limit} {type_phrase} matching: "
        f"{' '.join(qualifiers)}. "
        "Prefer official government domains (.gov, .us, state/county/city procurement portals) "
        "and recognized procurement aggregators (demandstar.com, bidnetdirect.com, bonfirehub.com). "
        "For each hit, extract structured fields. "
        "Use the source_url that links directly to the official announcement page. "
        "Set confidence='high' only when the page clearly states the project title, agency, "
        "and deadline; 'medium' when 1-2 fields are inferred; 'low' when most details are "
        "missing. Return ONLY high or medium confidence results. Set result_type appropriately "
        "(rfp/award/bond/budget/other). If you cannot find any matching opportunities, return "
        "an empty results array."
    )


# ---------------------------------------------------------------------------
# Public tool entry point
# ---------------------------------------------------------------------------


async def search_web_procurement(
    input_data: WebProcurementSearchInput,
) -> list | dict:
    """
    Search government and procurement portal websites for RFPs, bond elections,
    budget documents, and contract awards related to infrastructure projects.

    Uses Azure OpenAI's Responses API with the web_search_preview tool — the
    model runs a live web search and emits structured procurement records
    matching the JSON schema in a single call.

    Returns a list of normalised procurement records on success, or a
    structured error dict. Never raises.
    """
    tool_start = time.monotonic()

    azure_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
    azure_api_key = os.environ.get("AZURE_OPENAI_API_KEY", "")
    deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4.1-mini")

    if not azure_endpoint or not azure_api_key:
        emit_tool_call("search_web_procurement", (time.monotonic() - tool_start) * 1000, "error")
        return {
            "error": "AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_API_KEY not configured",
            "retriable": False,
            "source": "azure_openai",
        }

    instructions = _build_instructions(input_data)
    logger.info(
        "web_procurement search query=%r geography=%r sector=%r result_type=%r limit=%d",
        input_data.query, input_data.geography, input_data.sector,
        input_data.result_type, input_data.limit,
    )

    payload = {
        "model": deployment,
        "input": instructions,
        "tools": [{"type": "web_search_preview"}],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "procurement_results",
                "schema": _RESULTS_SCHEMA,
                "strict": True,
            },
        },
    }

    url = f"{azure_endpoint.rstrip('/')}/openai/v1/responses?api-version=preview"
    headers = {"api-key": azure_api_key, "Content-Type": "application/json"}

    api_start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            latency_ms = (time.monotonic() - api_start) * 1000
            if resp.status_code >= 400:
                body = resp.text
                emit_external_api("azure_openai", latency_ms, error_type=f"http_{resp.status_code}")
                logger.warning(
                    "web_procurement Azure OpenAI HTTP %d: %s",
                    resp.status_code, body[:300],
                )
                log_external_api_failure(
                    logger,
                    source="azure_openai",
                    tool_name="search_web_procurement",
                    status_code=resp.status_code,
                    body=body,
                )
                emit_tool_call("search_web_procurement", (time.monotonic() - tool_start) * 1000, "error")
                return {
                    "error": f"Azure OpenAI Responses API error: HTTP {resp.status_code}",
                    "retriable": resp.status_code >= 500 or resp.status_code == 429,
                    "source": "azure_openai",
                }
            emit_external_api("azure_openai", latency_ms)
            response_data = resp.json()
    except httpx.TimeoutException as exc:
        emit_external_api("azure_openai", (time.monotonic() - api_start) * 1000, error_type="timeout")
        emit_tool_call("search_web_procurement", (time.monotonic() - tool_start) * 1000, "error")
        log_external_api_failure(
            logger, source="azure_openai", tool_name="search_web_procurement", error=str(exc)
        )
        return {"error": "Azure OpenAI Responses API request timed out.", "retriable": True, "source": "azure_openai"}
    except httpx.RequestError as exc:
        emit_external_api("azure_openai", (time.monotonic() - api_start) * 1000, error_type="request_error")
        emit_tool_call("search_web_procurement", (time.monotonic() - tool_start) * 1000, "error")
        log_external_api_failure(
            logger, source="azure_openai", tool_name="search_web_procurement", error=str(exc)
        )
        return {"error": f"Azure OpenAI Responses API request failed: {exc}", "retriable": True, "source": "azure_openai"}
    except Exception as exc:
        emit_external_api("azure_openai", (time.monotonic() - api_start) * 1000, error_type="unexpected")
        emit_tool_call("search_web_procurement", (time.monotonic() - tool_start) * 1000, "error")
        logger.exception("Unexpected error calling Azure OpenAI Responses API")
        log_external_api_failure(
            logger, source="azure_openai", tool_name="search_web_procurement", error=str(exc)
        )
        return {"error": "Unexpected error calling Azure OpenAI.", "retriable": False, "source": "azure_openai"}

    # Responses API shape: { "output": [{type:"message", content:[{type:"output_text", text:"..."}]}] }
    # Concatenate every output_text content piece; the schema-constrained JSON
    # body is normally a single text item but we walk all of them defensively.
    output_text = ""
    for item in response_data.get("output", []):
        for part in item.get("content", []):
            if part.get("type") == "output_text":
                output_text += part.get("text") or ""

    if not output_text.strip():
        logger.warning("web_procurement: no output_text in response")
        emit_tool_call("search_web_procurement", (time.monotonic() - tool_start) * 1000, "success", result_count=0)
        return []

    try:
        parsed = json.loads(output_text)
        results = parsed.get("results", []) if isinstance(parsed, dict) else []
    except json.JSONDecodeError as exc:
        logger.warning("web_procurement: failed to parse model JSON")
        log_external_api_failure(
            logger,
            source="azure_openai",
            tool_name="search_web_procurement",
            error=str(exc),
            body=output_text,
        )
        emit_tool_call("search_web_procurement", (time.monotonic() - tool_start) * 1000, "error")
        return {"error": "Model returned malformed JSON", "retriable": True, "source": "azure_openai"}

    emit_tool_call(
        "search_web_procurement",
        (time.monotonic() - tool_start) * 1000,
        "success",
        result_count=len(results),
    )
    logger.info("web_procurement: %d results returned", len(results))
    return results
