import ddtrace.auto  # must be first import — monkey-patches httpx, openai, redis at import time

import logging
import os

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
        "EIA energy data, EPA SDWIS water system compliance data, "
        "TWDB 2026 State Water Plan projects, "
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
    data_series: str = "generation",
    year_from: int | None = None,
    year_to: int | None = None,
    fuel_types: list[str] | None = None,
) -> list | dict:
    """Query EIA for state-level energy generation and infrastructure data."""
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
    query_type: str,
    states: list[str] | None = None,
    counties: list[str] | None = None,
    planning_regions: list[str] | None = None,
    project_types: list[str] | None = None,
    system_types: list[str] | None = None,
    has_violations: bool | None = None,
    min_population_served: int | None = None,
    limit: int = 50,
) -> list | dict:
    """Query water infrastructure data: EPA SDWIS for compliance or TWDB 2026 State Water Plan for projects."""
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
    "search_project_knowledge",
    "draft_document",
]


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    """Liveness probe — returns service status and available tool names."""
    return JSONResponse({
        "status": "ok",
        "service": os.environ.get("DD_SERVICE", "infratools-mcp"),
        "tools": TOOL_NAMES,
    })


# The MCP server is the ASGI app; /mcp handles MCP protocol, /health handles probes
app = mcp.streamable_http_app()
