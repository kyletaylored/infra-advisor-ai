import ddtrace.auto  # must be first import — enables APM auto-instrumentation

import asyncio
import json
import logging
import os
import re
import time
from typing import Optional

import httpx
from openai import AzureOpenAI
from pydantic import BaseModel

from observability.metrics import emit_external_api, emit_tool_call

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TAVILY_SEARCH_URL = "https://api.tavily.com/search"

_INCLUDE_DOMAINS = [
    ".gov",
    ".us",
    "demandstar.com",
    "bidnetdirect.com",
    "bonfirehub.com",
]


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
# Query builder (unchanged logic — just passed to Tavily instead of Brave)
# ---------------------------------------------------------------------------


def _build_search_query(input_data: WebProcurementSearchInput) -> str:
    """Build a targeted search query string from the structured input."""
    parts = [input_data.query]

    if input_data.geography:
        # Add site: hints to bias results toward .gov / .us domains
        parts.append("site:.gov OR site:.us")

    sector_terms = {
        "transportation": "transportation infrastructure",
        "water": "water treatment infrastructure",
        "energy": "energy power infrastructure",
        "buildings": "commercial building construction",
        "environmental": "environmental remediation",
    }
    if input_data.sector and input_data.sector in sector_terms:
        parts.append(sector_terms[input_data.sector])

    if input_data.result_type == "rfp":
        parts.append('"request for proposals" OR "RFP" OR "solicitation"')
    elif input_data.result_type == "bond":
        parts.append('"bond election" OR "municipal bond"')
    elif input_data.result_type == "budget":
        parts.append('"infrastructure budget" OR "capital improvement plan"')

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Tavily search
# ---------------------------------------------------------------------------


async def _tavily_search(query: str, limit: int) -> list[dict] | dict:
    """
    Call the Tavily Search API and return a list of result dicts.

    Each item has keys: url, title, content (pre-fetched and cleaned by Tavily).
    Returns a dict with an 'error' key on failure.
    """
    tavily_api_key = os.environ.get("TAVILY_API_KEY", "")
    if not tavily_api_key:
        return {"error": "TAVILY_API_KEY not configured", "retriable": False}

    body = {
        "api_key": tavily_api_key,
        "query": query,
        "search_depth": "advanced",
        "include_domains": _INCLUDE_DOMAINS,
        "max_results": limit,
    }

    api_start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(TAVILY_SEARCH_URL, json=body)
            latency_ms = (time.monotonic() - api_start) * 1000

            if resp.status_code >= 400:
                emit_external_api("tavily", latency_ms, error_type=f"http_{resp.status_code}")
                return {
                    "error": f"Tavily Search API error: HTTP {resp.status_code}",
                    "retriable": resp.status_code >= 500,
                }

            emit_external_api("tavily", latency_ms)
            data = resp.json()

    except httpx.TimeoutException:
        latency_ms = (time.monotonic() - api_start) * 1000
        emit_external_api("tavily", latency_ms, error_type="timeout")
        return {"error": "Tavily Search API request timed out.", "retriable": True}

    except httpx.RequestError as exc:
        latency_ms = (time.monotonic() - api_start) * 1000
        emit_external_api("tavily", latency_ms, error_type="request_error")
        return {"error": f"Tavily Search API request failed: {exc}", "retriable": True}

    except Exception:
        latency_ms = (time.monotonic() - api_start) * 1000
        emit_external_api("tavily", latency_ms, error_type="unexpected")
        logger.exception("Unexpected error calling Tavily Search")
        return {"error": "Unexpected error calling Tavily Search.", "retriable": False}

    raw_results: list[dict] = data.get("results", [])
    return [
        {
            "url": r.get("url", ""),
            "title": r.get("title", ""),
            "content": r.get("content", ""),
        }
        for r in raw_results
    ]


# ---------------------------------------------------------------------------
# Azure OpenAI extraction (sync — run in executor)
# ---------------------------------------------------------------------------


def _extract_procurement_data(text: str, source_url: str) -> dict | None:
    """
    Sync call to Azure OpenAI for structured procurement data extraction.

    Returns a normalised dict or None if extraction fails, confidence is low,
    or result_type is "other".
    """
    try:
        client = AzureOpenAI(
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
            api_version="2025-01-01-preview",
        )
        deployment = os.environ.get("AZURE_OPENAI_EVAL_DEPLOYMENT_NAME", "gpt-4.1-nano")
        prompt = f"""Extract procurement information from this government webpage text.
Return ONLY valid JSON with these fields:
- agency_name (string or null)
- project_title (string or null)
- project_description (max 200 chars, string or null)
- estimated_value_usd (integer or null)
- deadline (ISO date string or null)
- contact_email (string or null)
- source_url (string - use "{source_url}")
- result_type ("rfp" | "award" | "bond" | "budget" | "other")
- confidence ("high" | "medium" | "low")

Return null for any field you cannot confidently determine.
Text: {text[:2500]}"""

        response = client.chat.completions.create(
            model=deployment,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=400,
        )
        content = response.choices[0].message.content or ""
        json_match = re.search(r"\{.*\}", content, re.DOTALL)
        if not json_match:
            return None
        data = json.loads(json_match.group())
        if data.get("confidence") == "low" or data.get("result_type") == "other":
            return None
        data["_source"] = "web_search"
        data["_search_engine"] = "Tavily"
        return data
    except Exception:
        logger.debug("Extraction failed for %s", source_url, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Public tool entry point
# ---------------------------------------------------------------------------


async def search_web_procurement(
    input_data: WebProcurementSearchInput,
) -> list | dict:
    """
    Search government and procurement portal websites for RFPs, bond elections,
    budget documents, and contract awards related to infrastructure projects.

    Uses Tavily Search to find relevant pages with pre-fetched content, then
    extracts structured procurement data using Azure OpenAI (gpt-4.1-nano).

    Returns a list of normalised procurement records on success, or a structured
    error dict. Never raises.
    """
    tool_start = time.monotonic()

    # Step 1 — Check for API key before doing any work
    if not os.environ.get("TAVILY_API_KEY"):
        emit_tool_call("search_web_procurement", (time.monotonic() - tool_start) * 1000, "error")
        return {"error": "TAVILY_API_KEY not configured", "retriable": False}

    # Step 2 — Build the search query
    query = _build_search_query(input_data)
    logger.info("web_procurement search query=%r limit=%d", query, input_data.limit)

    # Step 3 — Call Tavily (returns pre-fetched content — no separate page fetches needed)
    search_result = await _tavily_search(query, input_data.limit)
    if isinstance(search_result, dict) and "error" in search_result:
        emit_tool_call("search_web_procurement", (time.monotonic() - tool_start) * 1000, "error")
        return search_result

    if not search_result:
        logger.info("web_procurement: no results returned from Tavily")
        emit_tool_call("search_web_procurement", (time.monotonic() - tool_start) * 1000, "success", result_count=0)
        return []

    # Step 4 — Extract structured data from each result's pre-fetched content (run in executor)
    loop = asyncio.get_event_loop()
    extraction_tasks = [
        loop.run_in_executor(None, _extract_procurement_data, item["content"], item["url"])
        for item in search_result
    ]
    extraction_results = await asyncio.gather(*extraction_tasks, return_exceptions=False)

    # Step 5 — Collect non-None results
    results: list[dict] = [r for r in extraction_results if r is not None]

    emit_tool_call(
        "search_web_procurement",
        (time.monotonic() - tool_start) * 1000,
        "success",
        result_count=len(results),
    )
    logger.info("web_procurement: %d results from %d Tavily results", len(results), len(search_result))
    return results
