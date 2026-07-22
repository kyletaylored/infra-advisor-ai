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
    # dd.trace_id/dd.span_id placeholders are what actually make
    # DD_LOGS_INJECTION=true correlate a log line to its trace — ddtrace
    # patches LogRecord with these attributes regardless, but they never
    # reach the rendered output (and so are invisible to Datadog's log
    # pipeline) unless the format string references them explicitly. Found
    # missing while debugging why log_external_api_failure's log lines
    # showed up in Datadog Logs with no "View trace" correlation at all.
    format=(
        "%(asctime)s %(levelname)s [%(name)s] "
        "[dd.service=%(dd.service)s dd.env=%(dd.env)s dd.version=%(dd.version)s "
        "dd.trace_id=%(dd.trace_id)s dd.span_id=%(dd.span_id)s] - %(message)s"
    ),
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
    """FHWA National Bridge Inventory — every US public bridge over 20 ft (~617,000 records).

    Returns structure-level condition data: deck / superstructure / substructure ratings
    (0=failed → 9=excellent), overall BRIDGE_CONDITION (Good/Fair/Poor), LOWEST_RATING
    (worst of the three), scour-critical flag, ADT, location, year built. _source: 'FHWA NBI'.
    Coverage: all US states + DC + Puerto Rico. Refreshed annually by FHWA.

    Use when the user asks: structurally deficient bridges in <state/county>; bridges with
    sufficiency rating under N; high-traffic bridges needing inspection; scour-vulnerable
    bridges; oldest bridges in an area; bridge condition stats.
    Do NOT use for: rail bridges (NBI is highway only); culverts under 20 ft span;
    real-time inspection findings (data is annual); pedestrian-only bridges.

    state_code is a 2-CHARACTER FIPS STATE CODE — note leading zero for single-digit states.
    Common values: AL=01, AZ=04, AR=05, CA=06, CO=08, FL=12, GA=13, IL=17, LA=22, MS=28,
    NM=35, NY=36, NC=37, OK=40, TX=48, VA=51, WA=53.
    county_code is the 3-character FIPS county code within that state (e.g. Harris County TX=201).
    Returns up to 200 rows sorted by ascending LOWEST_RATING (worst first).
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
    """Federal disaster declaration history from OpenFEMA.

    Returns the official FEMA record of major-disaster, emergency, and fire-management
    declarations: declaration ID, incident type, declared date, affected counties, and
    program activations. _source: 'OpenFEMA'.
    Coverage: every US state + territory, 1953 to present. No API key required.

    Use when the user asks: how often does an area get hurricanes / floods / wildfires;
    what disasters affected the project area in the last N years; which counties have
    repeat flood declarations; FEMA Public Assistance funding history; multi-hazard
    exposure assessment for resilience planning.
    Do NOT use for: real-time / active disasters (this is historical); individual property
    damage data; FEMA flood-zone maps (different source); state/local emergency declarations
    not in the federal record.

    incident_types common values: 'Flood', 'Hurricane', 'Severe Storm', 'Tornado', 'Fire',
    'Earthquake', 'Drought', 'Winter Storm', 'Coastal Storm', 'Tropical Storm'.
    Returns up to 1000 records sorted by declarationDate descending.
    """
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
    """EIA state-level electricity generation, capacity, and fuel-mix data.

    _source: 'EIA'. Coverage: all US states, multi-year historical.
    Use for multi-state or non-Texas energy queries. For Texas-specific real-time grid
    storage, use get_ercot_energy_storage instead.

    data_series must be exactly one of:
      - "generation" — electricity generated per state/fuel type in MWh (default)
      - "capacity"   — installed generating capacity in MW
      - "fuel_mix"   — percentage share of generation by fuel type

    Use "fuel_mix" for "renewable share" or "% solar" questions.
    Use "generation" for raw MWh output questions.
    Use "capacity" for installed MW questions.
    fuel_types common values: 'solar', 'wind', 'nuclear', 'natural gas', 'coal', 'hydro'.
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
    """Hybrid semantic + keyword search against the firm's internal Azure AI Search knowledge base.

    Searches case studies, prior project SOWs, risk frameworks, document templates, and
    vetted best practices. _source: 'firm knowledge base'.

    ALWAYS call this BEFORE draft_document — it pulls relevant templates and prior project
    context that the draft tool needs to produce grounded output.
    Do NOT use for: live external data (use the domain-specific tools); internet search
    (use search_web_procurement); current procurement opportunities (use get_procurement_opportunities).

    document_types common values: 'water_plan_project', 'sow', 'risk_summary', 'case_study', 'best_practice'.
    domains common values: 'water', 'transportation', 'energy', 'environmental', 'business_development'.
    Returns ranked document chunks with content, document_type, domain, and relevance score.
    """
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
    """SAM.gov + grants.gov — ACTIVE federal contract opportunities and grants.

    Merges and deduplicates results from both sources sorted by deadline (soonest first).
    Each result tagged _source: 'SAM.gov' or 'grants.gov'. Requires SAMGOV_API_KEY.
    Defaults to the last 12 months — NEVER ask the user for a date range.

    BD PAIRING RULE: For business-development queries, always call get_contract_awards FIRST
    to understand who won similar work, then call this tool to see open opportunities.
    Do NOT use for: historical awards (use get_contract_awards); state/local RFPs
    (use search_web_procurement); bond elections (use search_web_procurement).

    opportunity_types: 'contract' | 'grant' | omit for both.
    naics_codes common AEC values: ['237310'] highway, ['237110'] water/sewer,
    ['237990'] heavy civil, ['541330'] engineering services, ['541310'] architecture.
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
    """USASpending.gov — HISTORICAL federal contract awards.

    Who won similar work, at what price, and for which agencies.
    _source: 'USASpending.gov'. No API key required. Default window: past 2 years.

    Use when the user asks: who won contracts for <work type>; typical award amounts for
    <NAICS> in <state>; incumbent contractors for <agency>; competitive intel before
    bidding; spending patterns by NAICS or agency; pricing benchmarks for SOW drafts.
    BD PAIRING RULE: ALWAYS call this BEFORE get_procurement_opportunities — understanding
    who won similar work informs positioning for open opportunities.
    Do NOT use for: active/open solicitations (use get_procurement_opportunities);
    state/local awards (federal only); contracts under $25K (USASpending threshold).

    naics_codes common AEC values: ['237310'] highway, ['237110'] water/sewer line,
    ['237990'] other heavy civil (bridges/dams), ['541330'] engineering, ['541310'] architecture.
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

    Uses Azure OpenAI's web_search_preview tool to find and extract structured procurement
    records from .gov and .us pages in one call. Each result has a confidence field
    ('high' or 'medium') — always flag medium-confidence results to the user for verification.
    No separate search API key required (uses AZURE_OPENAI_API_KEY).

    Use for: state/local government RFPs; bond election announcements; municipal budget
    procurement; opportunities NOT on SAM.gov or grants.gov.
    Do NOT use for: federal opportunities (use get_procurement_opportunities);
    historical federal awards (use get_contract_awards).

    sector: 'transportation' | 'water' | 'energy' | 'buildings' | 'environmental'
    result_type: 'rfp' | 'bond' | 'budget' | 'award' | 'any'
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
    """Render a structured consulting deliverable from a template + context dictionary.

    Returns Markdown ready for client review. DETERMINISTIC — no LLM invoked inside the tool.
    ALWAYS call search_project_knowledge FIRST to pull relevant templates and prior-project
    context, then pass the retrieved snippets into context here so the draft is grounded.

    document_type must be exactly one of:
      - 'scope_of_work'           → SOW with scope, deliverables, schedule, exclusions, assumptions
      - 'risk_summary'            → Top-5 risks ranked by likelihood × impact with mitigation language
      - 'cost_estimate_scaffold'  → Line-item table with placeholder costs for analyst to fill in
      - 'funding_positioning_memo'→ Grant/funding pursuit memo with eligibility, match requirements,
                                    key differentiators

    context: pass data from prior tool calls — e.g. {bridges:[...], contract_awards:[...],
    best_practices:[...]}. Template fields reference keys in this object.
    """
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
    # Web search now uses Azure OpenAI's web_search_preview tool — the same
    # AZURE_OPENAI_API_KEY drives every AI call in this service. No separate
    # vendor key required.
    azure_openai_key = os.environ.get("AZURE_OPENAI_API_KEY", "")
    return JSONResponse({
        "status": "ok",
        "service": os.environ.get("DD_SERVICE", "infratools-mcp"),
        "tools": TOOL_NAMES,
        "keys_configured": {
            "samgov": bool(samgov_key),
            "azure_openai": bool(azure_openai_key),
        },
    })


# The MCP server is the ASGI app; /mcp handles MCP protocol, /health handles probes
app = mcp.streamable_http_app()
