import ddtrace.auto  # must be first import — monkey-patches httpx, openai, redis at import time

import logging
import os
from typing import Literal

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

from tools.bridge_condition import BridgeConditionInput
from tools.bridge_condition import get_bridge_condition as _get_bridge_condition
from tools.disaster_history import DisasterHistoryInput
from tools.disaster_history import get_disaster_history as _get_disaster_history
from tools.energy_infrastructure import EnergyInfrastructureInput
from tools.energy_infrastructure import get_energy_infrastructure as _get_energy_infrastructure
from tools.water_infrastructure import WaterInfrastructureInput
from tools.water_infrastructure import get_water_infrastructure as _get_water_infrastructure
from tools.project_knowledge import ProjectKnowledgeInput
from tools.project_knowledge import search_project_knowledge as _search_project_knowledge
from tools.draft_document import DraftDocumentInput
from tools.draft_document import draft_document as _draft_document
from tools.ercot_energy import ERCOTEnergyStorageInput
from tools.ercot_energy import get_ercot_energy_storage as _get_ercot_energy_storage
from tools.txdot_open_data import TxDOTOpenDataInput
from tools.txdot_open_data import search_txdot_open_data as _search_txdot_open_data
from tools.procurement_opportunities import ProcurementOpportunitiesInput
from tools.procurement_opportunities import get_procurement_opportunities as _get_procurement_opportunities
from tools.contract_awards import ContractAwardsInput
from tools.contract_awards import get_contract_awards as _get_contract_awards
from tools.web_procurement_search import WebProcurementSearchInput
from tools.web_procurement_search import search_web_procurement as _search_web_procurement

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

# ─── MCP Server ───────────────────────────────────────────────────────────────

mcp = FastMCP(
    name="infratools",
    instructions=(
        "InfraTools MCP server for infrastructure consulting. "
        "Provides access to FHWA NBI bridge inventory, FEMA disaster declarations, "
        "EIA energy data, ERCOT Texas grid energy storage data, "
        "TxDOT Open Data (traffic counts, construction projects), "
        "EPA SDWIS water system compliance data, "
        "TWDB 2026 State Water Plan projects, "
        "SAM.gov and grants.gov federal procurement opportunities, "
        "USASpending.gov contract award intelligence, "
        "Brave-powered web search for state/local government RFPs, "
        "the firm's internal knowledge base, and document drafting."
    ),
    # Disable DNS rebinding protection — running inside K8s cluster, all hostnames are trusted
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    # Stateless mode: no session state between requests, avoids session cleanup 404s
    # when langchain_mcp_adapters creates a new connection per tool call
    stateless_http=True,
)


@mcp.tool()
async def get_bridge_condition(
    state_code: str,
    county_code: str | None = None,
    structure_number: str | None = None,
    min_adt: int | None = None,
    max_lowest_rating: int | None = None,
    structurally_deficient_only: bool = False,
    last_inspection_before: str | None = None,
    order_by: str = "LOWEST_RATING ASC",
    limit: int = 50,
) -> list | dict:
    """Query the FHWA National Bridge Inventory for bridges matching specified criteria.

    state_code must be a 2-digit FIPS numeric code, NOT a 2-letter abbreviation:
    TX=48, CA=06, FL=12, NY=36, LA=22, OK=40, AZ=04, CO=08, NM=35, AR=05.
    """
    return await _get_bridge_condition(
        BridgeConditionInput(
            state_code=state_code,
            county_code=county_code,
            structure_number=structure_number,
            min_adt=min_adt,
            max_lowest_rating=max_lowest_rating,
            structurally_deficient_only=structurally_deficient_only,
            last_inspection_before=last_inspection_before,
            order_by=order_by,
            limit=limit,
        )
    )


@mcp.tool()
async def get_disaster_history(
    states: list[str] | None = None,
    incident_types: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    infrastructure_keywords: list[str] | None = None,
    limit: int = 100,
) -> list | dict:
    """Query OpenFEMA for disaster declarations and public assistance data."""
    return await _get_disaster_history(
        DisasterHistoryInput(
            states=states,
            incident_types=incident_types,
            date_from=date_from,
            date_to=date_to,
            infrastructure_keywords=infrastructure_keywords,
            limit=limit,
        )
    )


@mcp.tool()
async def get_energy_infrastructure(
    states: list[str],
    data_series: Literal["generation", "capacity", "fuel_mix"] = "generation",
    year_from: int | None = None,
    year_to: int | None = None,
    fuel_types: list[str] | None = None,
) -> list | dict:
    """Query EIA for state-level energy generation and infrastructure data.

    data_series must be exactly one of:
      - "generation" — electricity generated per state/fuel type (default)
      - "capacity"   — installed generating capacity
      - "fuel_mix"   — share of generation by fuel type
    """
    return await _get_energy_infrastructure(
        EnergyInfrastructureInput(
            states=states,
            data_series=data_series,
            year_from=year_from,
            year_to=year_to,
            fuel_types=fuel_types,
        )
    )


@mcp.tool()
async def get_water_infrastructure(
    query_type: Literal["water_systems", "water_plan_projects", "violations"],
    states: list[str] | None = None,
    counties: list[str] | None = None,
    planning_regions: list[str] | None = None,
    project_types: list[str] | None = None,
    system_types: list[str] | None = None,
    has_violations: bool | None = None,
    min_population_served: int | None = None,
    limit: int = 50,
) -> list | dict:
    """Query water infrastructure data.

    query_type must be exactly one of:
      - "water_systems"      — EPA SDWIS public water system inventory
      - "water_plan_projects"— TWDB 2026 State Water Plan recommended projects
      - "violations"         — EPA SDWIS health-based SDWA violations

    Use "water_plan_projects" for any question about TWDB plans, recommended
    projects, supply strategies, or regional water planning.
    Use "water_systems" or "violations" for EPA compliance questions.
    """
    return await _get_water_infrastructure(
        WaterInfrastructureInput(
            query_type=query_type,
            states=states,
            counties=counties,
            planning_regions=planning_regions,
            project_types=project_types,
            system_types=system_types,
            has_violations=has_violations,
            min_population_served=min_population_served,
            limit=limit,
        )
    )


@mcp.tool()
async def search_project_knowledge(
    query: str,
    document_types: list[str] | None = None,
    domains: list[str] | None = None,
    top_k: int = 6,
) -> list | dict:
    """Hybrid semantic + keyword search against the firm's internal knowledge base."""
    return await _search_project_knowledge(
        ProjectKnowledgeInput(
            query=query,
            document_types=document_types,
            domains=domains,
            top_k=top_k,
        )
    )


@mcp.tool()
async def get_ercot_energy_storage(
    query_type: Literal["charging_data", "products"] = "charging_data",
    time_from: str | None = None,
    time_to: str | None = None,
    min_charging_mw: float | None = None,
    max_charging_mw: float | None = None,
    page: int = 1,
    size: int = 100,
) -> list | dict:
    """Query ERCOT's public data API for Energy Storage Resource (ESR) data.

    query_type must be exactly one of:
      - "charging_data" — 4-second ESR charging MW time-series (default)
      - "products"      — list available ERCOT public data product IDs

    time_from / time_to accept ISO-8601 strings e.g. "2024-06-01T00:00:00".
    This tool is Texas-specific — ERCOT covers ~90% of the Texas grid.
    Use get_energy_infrastructure for multi-state EIA data.
    """
    return await _get_ercot_energy_storage(
        ERCOTEnergyStorageInput(
            query_type=query_type,
            time_from=time_from,
            time_to=time_to,
            min_charging_mw=min_charging_mw,
            max_charging_mw=max_charging_mw,
            page=page,
            size=size,
        )
    )


@mcp.tool()
async def search_txdot_open_data(
    query_type: Literal["catalog_search", "traffic_counts", "construction_projects"] = "catalog_search",
    query: str = "",
    county: str | None = None,
    limit: int = 20,
    page: int = 1,
) -> list | dict:
    """Search the TxDOT Open Data portal (ArcGIS Hub) for Texas transportation datasets.

    query_type must be exactly one of:
      - "catalog_search"       — free-text search across all TxDOT datasets (requires query)
      - "traffic_counts"       — Annual Average Daily Traffic (AADT) count datasets
      - "construction_projects"— TxDOT highway construction and maintenance project datasets

    county: optional Texas county name to narrow results (e.g. "Harris", "Travis").
    Returns dataset metadata including title, description, url, and type.
    Texas-specific — all data is from the TxDOT Open Data portal.
    """
    return await _search_txdot_open_data(
        TxDOTOpenDataInput(
            query_type=query_type,
            query=query,
            county=county,
            limit=limit,
            page=page,
        )
    )


@mcp.tool()
async def get_procurement_opportunities(
    query: str,
    geography: str | None = None,
    naics_codes: list[str] | None = None,
    min_value_usd: int | None = None,
    max_value_usd: int | None = None,
    opportunity_types: list[str] | None = None,
    limit: int = 20,
) -> list | dict:
    """Search SAM.gov and grants.gov for active federal contract opportunities and grants.

    Merges results from both sources sorted by deadline (soonest first).
    Each result is tagged with _source: "SAM.gov" or "grants.gov".
    Requires SAMGOV_API_KEY env var (free at api.sam.gov — key includes SAM- prefix).
    opportunity_types: filter to "contract", "grant", or omit for both.
    """
    return await _get_procurement_opportunities(
        ProcurementOpportunitiesInput(
            query=query,
            geography=geography,
            naics_codes=naics_codes,
            min_value_usd=min_value_usd,
            max_value_usd=max_value_usd,
            opportunity_types=opportunity_types,
            limit=limit,
        )
    )


@mcp.tool()
async def get_contract_awards(
    query: str,
    geography: str | None = None,
    naics_codes: list[str] | None = None,
    agency_names: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    min_award_usd: int | None = None,
    limit: int = 25,
) -> list | dict:
    """Search USASpending.gov for historical federal contract awards.

    Returns competitive intelligence: who won similar work, at what price, and for which agencies.
    Each result tagged _source: "USASpending.gov". No API key required.
    date_from / date_to: ISO date strings (default: past 2 years through today).
    """
    return await _get_contract_awards(
        ContractAwardsInput(
            query=query,
            geography=geography,
            naics_codes=naics_codes,
            agency_names=agency_names,
            date_from=date_from,
            date_to=date_to,
            min_award_usd=min_award_usd,
            limit=limit,
        )
    )


@mcp.tool()
async def search_web_procurement(
    query: str,
    geography: str | None = None,
    sector: str | None = None,
    result_type: str | None = None,
    limit: int = 8,
) -> list | dict:
    """Search government websites for state/local RFPs, bond elections, and budget announcements.

    Uses Brave Search to find .gov and .us procurement pages, then extracts structured
    data using gpt-4.1-nano. Each result has a confidence field ("high" or "medium").
    Requires BRAVE_SEARCH_API_KEY env var.
    sector: "transportation" | "water" | "energy" | "buildings" | "environmental"
    result_type: "rfp" | "bond" | "budget" | "award" | "any"
    Returns _partial_results: true if some page fetches timed out.
    """
    return await _search_web_procurement(
        WebProcurementSearchInput(
            query=query,
            geography=geography,
            sector=sector,
            result_type=result_type,
            limit=limit,
        )
    )


@mcp.tool()
async def draft_document(
    document_type: str,
    context: dict,
    project_name: str | None = None,
    client_name: str | None = None,
    notes: str | None = None,
) -> str | dict:
    """Generate a structured document scaffold (SOW, risk summary, cost estimate, or funding memo)."""
    return await _draft_document(
        DraftDocumentInput(
            document_type=document_type,
            context=context,
            project_name=project_name,
            client_name=client_name,
            notes=notes,
        )
    )


# ─── Health endpoint (custom route on the MCP server) ─────────────────────────

TOOL_NAMES = [
    "get_bridge_condition",
    "get_disaster_history",
    "get_energy_infrastructure",
    "get_water_infrastructure",
    "get_ercot_energy_storage",
    "search_txdot_open_data",
    "search_project_knowledge",
    "draft_document",
    "get_procurement_opportunities",
    "get_contract_awards",
    "search_web_procurement",
]


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    """Liveness probe — returns service status, available tool names, and API key status."""
    samgov_key = os.environ.get("SAMGOV_API_KEY", "")
    tavily_key = os.environ.get("TAVILY_API_KEY", "")
    return JSONResponse({
        "status": "ok",
        "service": os.environ.get("DD_SERVICE", "infratools-mcp"),
        "tools": TOOL_NAMES,
        "keys_configured": {
            "samgov": bool(samgov_key),
            "tavily": bool(tavily_key),
        },
    })


# The MCP server is the ASGI app; /mcp handles MCP protocol, /health handles probes
app = mcp.streamable_http_app()
