"""Tests for the get_water_infrastructure tool.

Strategy
--------
* EPA SDWIS calls are mocked with respx so no real network access is required.
* Azure AI Search calls are patched via unittest.mock so no real credentials
  are required for TWDB water plan project queries.
* All tests pass with DD_AGENT_HOST=localhost and no real Datadog agent.

Coverage
--------
* water_systems query returns >= 1 result with PWSID field
* Every EPA result has _source == "EPA_SDWIS"
* water_plan_projects query returns results from mocked AI Search
* Every TWDB result has _source == "TWDB_2026_State_Water_Plan"
* min_population_served filter is applied correctly
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Environment — must be set before any ddtrace imports
# ---------------------------------------------------------------------------
os.environ.setdefault("DD_AGENT_HOST", "localhost")
os.environ.setdefault("DD_TRACE_ENABLED", "false")
os.environ.setdefault("DD_DOGSTATSD_PORT", "8125")
os.environ.setdefault("EPA_SDWIS_BASE_URL", "https://enviro.epa.gov/enviro/efservice")
os.environ.setdefault("AZURE_SEARCH_ENDPOINT", "https://mock.search.windows.net")
os.environ.setdefault("AZURE_SEARCH_API_KEY", "mock-key")
os.environ.setdefault("AZURE_SEARCH_INDEX_NAME", "infra-advisor-knowledge")
os.environ.setdefault("AZURE_OPENAI_ENDPOINT", "https://mock.openai.azure.com")
os.environ.setdefault("AZURE_OPENAI_API_KEY", "mock-key")

# ---------------------------------------------------------------------------
# Make the src package tree importable when running via `uv run pytest`
# from the services/mcp-server root directory.
# ---------------------------------------------------------------------------
_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import respx  # noqa: E402
from httpx import Response  # noqa: E402

from tools.water_infrastructure import (  # noqa: E402
    EPA_SOURCE_LABEL,
    TWDB_SOURCE_LABEL,
    WaterInfrastructureInput,
    get_water_infrastructure,
)

# ---------------------------------------------------------------------------
# Fake EPA SDWIS endpoint base
# ---------------------------------------------------------------------------

EPA_BASE = "https://enviro.epa.gov/enviro/efservice"
EPA_TX_CWS_URL = f"{EPA_BASE}/WATER_SYSTEM/STATE_CODE/TX/PWS_TYPE_CODE/CWS/JSON"


# ---------------------------------------------------------------------------
# Helpers — build realistic EPA SDWIS water system records
# ---------------------------------------------------------------------------


def _make_water_system(
    pwsid: str = "TX0140076",
    pws_name: str = "CITY OF SAN ANTONIO WATER SYSTEM",
    city: str = "SAN ANTONIO",
    county: str = "BEXAR",
    population: int = 1_400_000,
    source_code: str = "GU",
    pws_type: str = "CWS",
) -> dict:
    return {
        "PWSID": pwsid,
        "PWS_NAME": pws_name,
        "STATE_CODE": "TX",
        "CITY_NAME": city,
        "COUNTY_SERVED": county,
        "POPULATION_SERVED_COUNT": population,
        "PRIMARY_SOURCE_CODE": source_code,
        "PWS_TYPE_CODE": pws_type,
        "LAST_INSPECTION_DATE": "2022-09-01",
    }


def _make_violation(pwsid: str = "TX0140076", status: str = "OPEN") -> dict:
    return {
        "PWSID": pwsid,
        "VIOLATION_STATUS": status,
        "IS_HEALTH_BASED_IND": "Y",
    }


# ---------------------------------------------------------------------------
# Fake Azure AI Search result object
# ---------------------------------------------------------------------------


class _FakeSearchResult:
    """Mimics the attribute-access interface of azure.search.documents SearchResult."""

    def __init__(
        self,
        content: str = "TWDB 2026 Water Plan — Region M: Desalination project in Corpus Christi",
        source: str = "TWDB_2026_State_Water_Plan",
        document_type: str = "water_plan_project",
        domain: str = "water",
        score: float = 0.92,
        source_url=None,
    ):
        self.content = content
        self.source = source
        self.document_type = document_type
        self.domain = domain
        self.source_url = source_url
        # Azure SDK exposes the score via the @search.score attribute
        object.__setattr__(self, "@search.score", score)

    def __getattr__(self, item):
        return None  # default for fields not explicitly set


# ---------------------------------------------------------------------------
# Helper — return patch context managers for AI Search
# Since azure.search.documents is installed, SearchClient is imported at
# module level in water_infrastructure.py. We patch it there.
# ---------------------------------------------------------------------------


def _patch_search(mock_client):
    """Patch SearchClient and AzureKeyCredential in the tools.water_infrastructure module."""
    cm_client = patch("tools.water_infrastructure.SearchClient", return_value=mock_client)
    cm_cred = patch("tools.water_infrastructure.AzureKeyCredential", return_value=MagicMock())
    return cm_client, cm_cred


# ---------------------------------------------------------------------------
# Tests — EPA SDWIS (water_systems)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_water_systems_texas_returns_results():
    """water_systems query for Texas must return >= 1 result with PWSID field."""
    systems = [_make_water_system(), _make_water_system(pwsid="TX0140099", city="AUSTIN")]
    respx.get(EPA_TX_CWS_URL).mock(return_value=Response(200, json=systems))

    inp = WaterInfrastructureInput(query_type="water_systems", states=["TX"])
    result = await get_water_infrastructure(inp)

    assert isinstance(result, list), "Expected a list of water system dicts"
    assert len(result) >= 1, "Expected at least one result for Texas CWS query"
    assert "pwsid" in result[0], "Result must include 'pwsid' field"
    assert result[0]["pwsid"] == "TX0140076"


@pytest.mark.asyncio
@respx.mock
async def test_every_epa_result_has_source_field():
    """Every result from a water_systems query MUST have _source == 'EPA_SDWIS'."""
    systems = [
        _make_water_system(pwsid="TX0000001"),
        _make_water_system(pwsid="TX0000002"),
        _make_water_system(pwsid="TX0000003"),
    ]
    respx.get(EPA_TX_CWS_URL).mock(return_value=Response(200, json=systems))

    inp = WaterInfrastructureInput(query_type="water_systems", states=["TX"])
    result = await get_water_infrastructure(inp)

    assert len(result) >= 1
    for r in result:
        assert "_source" in r, f"Result missing _source field: {r}"
        assert r["_source"] == EPA_SOURCE_LABEL, (
            f"Expected _source='{EPA_SOURCE_LABEL}', got '{r['_source']}'"
        )


@pytest.mark.asyncio
@respx.mock
async def test_min_population_served_filter_applied():
    """Results below min_population_served must be excluded from the response."""
    systems = [
        _make_water_system(pwsid="TX0000001", population=500_000),
        _make_water_system(pwsid="TX0000002", population=5_000),  # below threshold
        _make_water_system(pwsid="TX0000003", population=250_000),
    ]
    respx.get(EPA_TX_CWS_URL).mock(return_value=Response(200, json=systems))

    inp = WaterInfrastructureInput(
        query_type="water_systems",
        states=["TX"],
        min_population_served=10_000,
    )
    result = await get_water_infrastructure(inp)

    assert isinstance(result, list)
    pwsids = [r["pwsid"] for r in result]
    assert "TX0000002" not in pwsids, "System with population < 10,000 should be filtered"
    assert "TX0000001" in pwsids, "System with population >= 10,000 must be included"
    assert "TX0000003" in pwsids, "System with population >= 10,000 must be included"


@pytest.mark.asyncio
@respx.mock
async def test_water_systems_required_fields_present():
    """Every result must contain system_name, pwsid, city, county, population_served,
    primary_source_type, and _source."""
    systems = [_make_water_system()]
    respx.get(EPA_TX_CWS_URL).mock(return_value=Response(200, json=systems))

    inp = WaterInfrastructureInput(query_type="water_systems", states=["TX"])
    result = await get_water_infrastructure(inp)

    assert len(result) == 1
    system = result[0]
    required_fields = [
        "system_name",
        "pwsid",
        "city",
        "county",
        "population_served",
        "primary_source_type",
        "_source",
    ]
    for field in required_fields:
        assert field in system, f"Missing required field: {field}"


@pytest.mark.asyncio
@respx.mock
async def test_water_systems_empty_returns_empty_list():
    """A state with no CWS records should return an empty list (no error)."""
    respx.get(f"{EPA_BASE}/WATER_SYSTEM/STATE_CODE/WY/PWS_TYPE_CODE/CWS/JSON").mock(
        return_value=Response(200, json=[])
    )

    inp = WaterInfrastructureInput(query_type="water_systems", states=["WY"])
    result = await get_water_infrastructure(inp)

    assert result == []


@pytest.mark.asyncio
async def test_water_systems_missing_states_returns_error():
    """Omitting states for water_systems must return a structured error."""
    inp = WaterInfrastructureInput(query_type="water_systems", states=[])
    result = await get_water_infrastructure(inp)

    assert isinstance(result, dict), "Expected an error dict when states is empty"
    assert "error" in result


# ---------------------------------------------------------------------------
# Tests — EPA SDWIS (violations)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_violations_query_attaches_open_count():
    """A 'violations' query must fetch violation counts and filter to systems with
    open violations when has_violations=True."""
    system = _make_water_system(pwsid="TX0140076")
    respx.get(EPA_TX_CWS_URL).mock(return_value=Response(200, json=[system]))
    violations_url = f"{EPA_BASE}/SDWA_VIOLATIONS/PWSID/TX0140076/IS_HEALTH_BASED_IND/Y/JSON"
    respx.get(violations_url).mock(
        return_value=Response(200, json=[_make_violation("TX0140076", "OPEN")])
    )

    inp = WaterInfrastructureInput(
        query_type="violations",
        states=["TX"],
        has_violations=True,
    )
    result = await get_water_infrastructure(inp)

    assert isinstance(result, list)
    assert len(result) >= 1
    assert result[0]["open_violation_count"] is not None
    assert result[0]["open_violation_count"] >= 1
    assert result[0]["_source"] == EPA_SOURCE_LABEL


@pytest.mark.asyncio
@respx.mock
async def test_violations_source_field_present():
    """Every violation query result must carry _source == 'EPA_SDWIS'."""
    systems = [_make_water_system(pwsid="TX9990001"), _make_water_system(pwsid="TX9990002")]
    respx.get(EPA_TX_CWS_URL).mock(return_value=Response(200, json=systems))

    for pwsid in ("TX9990001", "TX9990002"):
        url = f"{EPA_BASE}/SDWA_VIOLATIONS/PWSID/{pwsid}/IS_HEALTH_BASED_IND/Y/JSON"
        respx.get(url).mock(return_value=Response(200, json=[]))

    inp = WaterInfrastructureInput(query_type="violations", states=["TX"], has_violations=False)
    result = await get_water_infrastructure(inp)

    for r in result:
        assert r["_source"] == EPA_SOURCE_LABEL


# ---------------------------------------------------------------------------
# Tests — TWDB water_plan_projects (Azure AI Search mocked)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_twdb_water_plan_projects_returns_results():
    """water_plan_projects query must return >= 1 result from the mocked AI Search."""
    mock_results = [
        _FakeSearchResult(
            content="TWDB 2026 Water Plan — Region M: Seawater desalination, Corpus Christi",
            score=0.94,
        ),
        _FakeSearchResult(
            content="TWDB 2026 Water Plan — Region K: Aquifer storage, San Antonio",
            score=0.87,
        ),
    ]
    mock_client = MagicMock()
    mock_client.search.return_value = iter(mock_results)
    cm_client, cm_cred = _patch_search(mock_client)

    with cm_client, cm_cred:
        inp = WaterInfrastructureInput(query_type="water_plan_projects", planning_regions=["M"])
        result = await get_water_infrastructure(inp)

    assert isinstance(result, list)
    assert len(result) >= 1


@pytest.mark.asyncio
async def test_every_twdb_result_has_source_field():
    """CRITICAL: every water_plan_projects result MUST have
    _source == 'TWDB_2026_State_Water_Plan'."""
    mock_results = [
        _FakeSearchResult(score=0.91),
        _FakeSearchResult(score=0.88),
        _FakeSearchResult(score=0.75),
    ]
    mock_client = MagicMock()
    mock_client.search.return_value = iter(mock_results)
    cm_client, cm_cred = _patch_search(mock_client)

    with cm_client, cm_cred:
        inp = WaterInfrastructureInput(query_type="water_plan_projects")
        result = await get_water_infrastructure(inp)

    assert len(result) >= 1
    for r in result:
        assert "_source" in r, f"Result missing _source field: {r}"
        assert r["_source"] == TWDB_SOURCE_LABEL, (
            f"Expected _source='{TWDB_SOURCE_LABEL}', got '{r['_source']}'"
        )


@pytest.mark.asyncio
async def test_twdb_result_has_content_field():
    """Every TWDB result must include a non-empty 'content' field."""
    mock_results = [
        _FakeSearchResult(content="Region M desalination project — Corpus Christi, $450M")
    ]
    mock_client = MagicMock()
    mock_client.search.return_value = iter(mock_results)
    cm_client, cm_cred = _patch_search(mock_client)

    with cm_client, cm_cred:
        inp = WaterInfrastructureInput(query_type="water_plan_projects", counties=["Nueces"])
        result = await get_water_infrastructure(inp)

    assert len(result) >= 1
    assert result[0]["content"] != ""


@pytest.mark.asyncio
async def test_twdb_planning_region_filter_passed_to_search():
    """Planning region codes must appear in the search_text passed to
    SearchClient.search() so they influence keyword ranking."""
    mock_client = MagicMock()
    mock_client.search.return_value = iter([_FakeSearchResult()])
    cm_client, cm_cred = _patch_search(mock_client)

    with cm_client, cm_cred:
        inp = WaterInfrastructureInput(
            query_type="water_plan_projects",
            planning_regions=["C", "G"],
        )
        await get_water_infrastructure(inp)

    call_kwargs = mock_client.search.call_args
    search_text_arg = call_kwargs.kwargs.get("search_text") or call_kwargs.args[0]
    assert "C" in search_text_arg or "region C" in search_text_arg
    assert "G" in search_text_arg or "region G" in search_text_arg


@pytest.mark.asyncio
async def test_twdb_limit_respected():
    """The limit parameter must be passed as 'top' to SearchClient.search()."""
    mock_client = MagicMock()
    mock_client.search.return_value = iter([_FakeSearchResult()] * 5)
    cm_client, cm_cred = _patch_search(mock_client)

    with cm_client, cm_cred:
        inp = WaterInfrastructureInput(query_type="water_plan_projects", limit=5)
        await get_water_infrastructure(inp)

    call_kwargs = mock_client.search.call_args
    assert call_kwargs.kwargs.get("top") == 5


# ---------------------------------------------------------------------------
# Tests — DD metric emission (DogStatsd mocked)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_dd_metrics_emitted_for_epa_query():
    """EPA SDWIS queries must emit mcp.external_api.latency_ms{source:epa_sdwis}
    and mcp.tool.calls{tool:get_water_infrastructure}."""
    systems = [_make_water_system()]
    respx.get(EPA_TX_CWS_URL).mock(return_value=Response(200, json=systems))

    mock_statsd = MagicMock()
    with patch("observability.metrics.statsd", mock_statsd):
        inp = WaterInfrastructureInput(query_type="water_systems", states=["TX"])
        await get_water_infrastructure(inp)

    gauge_calls = [str(c) for c in mock_statsd.gauge.call_args_list]
    assert any("epa_sdwis" in c for c in gauge_calls), (
        "Expected mcp.external_api.latency_ms with source:epa_sdwis to be emitted"
    )

    increment_calls = [str(c) for c in mock_statsd.increment.call_args_list]
    assert any("mcp.tool.calls" in c for c in increment_calls), (
        "Expected mcp.tool.calls metric to be incremented"
    )


@pytest.mark.asyncio
async def test_dd_metrics_emitted_for_twdb_query():
    """TWDB water plan project queries must emit
    mcp.external_api.latency_ms{source:twdb}."""
    mock_client = MagicMock()
    mock_client.search.return_value = iter([_FakeSearchResult()])
    cm_client, cm_cred = _patch_search(mock_client)
    mock_statsd = MagicMock()

    with cm_client, cm_cred, patch("observability.metrics.statsd", mock_statsd):
        inp = WaterInfrastructureInput(query_type="water_plan_projects")
        await get_water_infrastructure(inp)

    gauge_calls = [str(c) for c in mock_statsd.gauge.call_args_list]
    assert any("twdb" in c for c in gauge_calls), (
        "Expected mcp.external_api.latency_ms with source:twdb to be emitted"
    )
