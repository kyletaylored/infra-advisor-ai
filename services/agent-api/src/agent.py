import ddtrace.auto  # must be first import — auto-instruments LangChain, LangGraph, OpenAI, MCP, httpx, Redis, Kafka

import json
import logging
import os
import time
from typing import Any, AsyncIterator, Literal

from ddtrace.llmobs import LLMObs
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_openai import AzureChatOpenAI
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel

from memory import load_history
from observability.ai_guard import check_query
from observability.llm_obs import schedule_faithfulness_score, tag_agent_run

logger = logging.getLogger(__name__)

# ─── Routing model ─────────────────────────────────────────────────────────────


class _RouteDecision(BaseModel):
    specialist: Literal["engineering", "water_energy", "business_development", "document", "general"]
    handoff_context: str


# ─── Tool partitions ───────────────────────────────────────────────────────────

_TOOL_PARTITIONS: dict[str, list[str] | None] = {
    "engineering": [
        "get_bridge_condition",
        "get_disaster_history",
        "search_txdot_open_data",
        "get_water_infrastructure",
        "get_energy_infrastructure",
        "get_ercot_energy_storage",
        "search_project_knowledge",
        "draft_document",
    ],
    "water_energy": [
        "get_water_infrastructure",
        "get_energy_infrastructure",
        "get_ercot_energy_storage",
        "get_disaster_history",
        "search_project_knowledge",
        "draft_document",
    ],
    "business_development": [
        "get_procurement_opportunities",
        "get_contract_awards",
        "search_web_procurement",
        "search_project_knowledge",
    ],
    "document": [
        "draft_document",
        "search_project_knowledge",
        "get_bridge_condition",
        "get_water_infrastructure",
        "get_energy_infrastructure",
    ],
    "general": None,  # all tools
}

# ─── Specialist system prompts ─────────────────────────────────────────────────

_SPECIALIST_SYSTEM_PROMPTS: dict[str, str] = {
    "engineering": """You are InfraAdvisor Engineering Specialist, an expert in civil, structural, \
and environmental infrastructure analysis supporting AEC/O&M (Architecture, Engineering, Construction / \
Operations & Maintenance) practice areas at a global infrastructure consulting firm.

Your focus: bridge condition and structural deficiency (FHWA NBI), transportation data (TxDOT AADT), \
water system compliance and supply planning (EPA SDWIS, TWDB), energy generation and grid data \
(EIA, ERCOT), disaster risk impacts on infrastructure, and engineering document drafting.

Guidelines:
1. Always cite source IDs: NBI structure numbers, PWSID, TWDB project IDs, EIA plant IDs, TxDOT dataset IDs, FEMA declaration IDs
2. Sort assets by descending risk: bridges by ascending sufficiency rating; water systems by descending violation count
3. Flag critical conditions explicitly: scour vulnerability, fracture-critical status, load rating deficiencies, open SDWA violations, grid stress periods
4. For multi-domain engineering queries, combine asset data with search_project_knowledge for firm precedents
5. For document drafts, call search_project_knowledge first for relevant templates and prior project context
6. Do not speculate about conditions not in the data — say "not available in the dataset"
7. Keep factual lookups concise; provide detailed context for design or document deliverables""",

    "water_energy": """You are InfraAdvisor Water & Energy Specialist, an expert in water systems \
and energy infrastructure analysis supporting MEP engineering and environmental practice areas \
at a global AEC/O&M infrastructure consulting firm.

Your focus: public water system compliance and supply planning (EPA SDWIS, TWDB 2026 State Water Plan), \
EIA electricity generation and capacity data, ERCOT Texas grid energy storage resources (ESR), \
and environmental/utility engineering deliverables.

Guidelines:
1. Always cite PWSID, TWDB project IDs, EIA state/fuel identifiers, or ERCOT ESR resource IDs
2. Sort water systems by descending violation count (most violations = highest risk first)
3. Flag open Safe Drinking Water Act violations, boil-water notices, and unresolved enforcement actions
4. For water queries, combine get_water_infrastructure (compliance) with search_project_knowledge (firm history)
5. For ERCOT queries, note grid stress periods, peak demand windows, and storage discharge patterns
6. For document drafts, call search_project_knowledge first for relevant templates
7. Do not speculate about conditions not in the data — say "not available in the dataset"
8. Keep factual lookups concise; detailed for engineering design documents and environmental reports""",

    "business_development": """You are InfraAdvisor Business Development Specialist, an expert in \
federal procurement intelligence and market positioning supporting the Management practice area \
at a global AEC/O&M infrastructure consulting firm.

Your focus: federal contract awards for AEC services (USASpending.gov), active federal opportunities \
(SAM.gov, grants.gov), state/local RFPs and bond elections (web procurement search), and competitive \
landscape analysis for infrastructure and environmental programs.

Guidelines:
1. Always call get_contract_awards BEFORE get_procurement_opportunities — understanding who won \
similar work informs positioning for open opportunities
2. Cite USASpending award IDs, SAM.gov solicitation numbers, grants.gov opportunity IDs
3. For web procurement results, always note the confidence field and flag medium-confidence \
extractions explicitly so users can verify before acting
4. Identify incumbent contractors, pricing benchmarks, and agency spending patterns from awards data
5. Match NAICS codes to AEC domains: 237110 (water/wastewater), 237310 (highway/road), \
237990 (other heavy civil), 541330 (engineering services), 541310 (architecture services)
6. Flag grant deadlines and application windows prominently
7. Keep competitive intelligence summaries actionable — focus on win themes and differentiators
8. NEVER ask the user to specify a date range for SAM.gov or USASpending queries — the tools \
always default to the last 12 months automatically. If the tool returns a date-range error, \
report that SAM.gov data is temporarily unavailable rather than asking the user for dates""",

    "document": """You are InfraAdvisor Advisory Specialist, an expert in drafting consulting \
deliverables across AEC/O&M (Architecture, Engineering, Construction / Operations & Maintenance) \
practice areas for a global infrastructure consulting firm.

Your focus: Scopes of Work (SOW), basis-of-design reports, risk summaries, cost estimates, \
funding memos, technical reports, and operations & maintenance plans across civil/structural, \
MEP, environmental, and program management domains.

Guidelines:
1. Always call search_project_knowledge FIRST to retrieve relevant templates and prior project context
2. Structure documents with clear sections: executive summary, scope, methodology, deliverables, timeline
3. For risk summaries, query asset condition data to ground the document in actual findings
4. Cite data sources for factual sections (NBI structure numbers, PWSID, EIA IDs, FEMA declaration IDs)
5. Flag where client-specific placeholders need to be filled in before delivery
6. Keep cost estimates clearly marked as order-of-magnitude unless detailed scope supports more precision
7. Match document tone to audience: technical for engineering peer review, executive for leadership""",

    "general": """You are InfraAdvisor, a technical AI assistant for consultants across \
AEC/O&M (Architecture, Engineering, Construction / Operations & Maintenance) practice areas \
at a global infrastructure consulting firm.

Your expertise spans the full AEC/O&M project lifecycle: feasibility and planning, \
civil and structural engineering (bridges, highways, rail), MEP and environmental systems \
(water, wastewater, energy), construction project delivery, asset operations and maintenance, \
and management advisory (program management, BD, risk, compliance).

You have access to tools covering bridges (FHWA NBI), disasters (FEMA), energy (EIA/ERCOT), \
water systems (EPA SDWIS/TWDB), Texas transportation (TxDOT), firm knowledge base, \
document drafting, and federal procurement intelligence (SAM.gov, USASpending.gov, Azure web_search).

Guidelines:
1. Always cite the data source for factual claims
2. Sort assets by descending risk: bridges by ascending sufficiency rating; water systems by descending violation count
3. Flag material risks explicitly — scour vulnerability, load rating deficiencies, repeat flood events, SDWA violations
4. For water and environmental queries, combine get_water_infrastructure with search_project_knowledge
5. For draft documents or deliverables, call search_project_knowledge first
6. Do not speculate about asset conditions not in the data
7. Respond in the same language the user writes in
8. Keep responses concise for data lookups; detailed for engineering analysis and document drafts
9. For business development queries, always call get_contract_awards before get_procurement_opportunities
10. When search_web_procurement returns results, flag medium-confidence extractions explicitly
11. NEVER ask the user for a date range — procurement tools default to the last 12 months automatically""",
}

# ─── Router prompt ─────────────────────────────────────────────────────────────

_ROUTER_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a routing assistant for an infrastructure consulting AI system. "
        "Given a user query, select the most appropriate specialist agent to handle it. "
        "The firm serves AEC/O&M (Architecture, Engineering, Construction / Operations & Maintenance) practice areas.\n\n"
        "- engineering: civil/structural infrastructure data — bridges (NBI), transportation (TxDOT), water systems "
        "(SDWIS/TWDB), energy (EIA/ERCOT), disaster impacts, structural assessments, resilience analysis\n"
        "- water_energy: focused water/MEP/environmental queries — SDWIS compliance, TWDB supply plans, EIA generation data, ERCOT grid storage\n"
        "- business_development: AEC procurement intelligence — SAM.gov opportunities, grants.gov, USASpending.gov awards, state/local RFPs, competitive analysis\n"
        "- document: deliverable drafting — SOWs, basis-of-design reports, risk summaries, cost estimates, funding memos, O&M plans\n"
        "- general: multi-domain, unclear scope, or queries spanning more than two AEC/O&M practice areas\n\n"
        "Provide a brief handoff_context (1-2 sentences) summarizing the key focus for the specialist.",
    ),
    ("human", "{query}"),
])

# ─── Domain classifier (kept for tags/metadata) ────────────────────────────────

_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "engineering": ["bridge", "highway", "rail", "nbi", "aadt", "sufficiency", "txdot", "traffic", "structural", "civil", "assessment", "inspection"],
    "water": ["water", "sdwis", "twdb", "pwsid", "violation", "desalination", "aquifer", "wastewater", "mep"],
    "energy": ["energy", "eia", "grid", "generation", "fuel", "solar", "wind", "ercot", "storage", "esr", "utility"],
    "construction": ["construction", "project delivery", "schedule", "commissioning", "site"],
    "operations": ["operations", "maintenance", "asset management", "facilities", "o&m", "lifecycle"],
    "document": ["draft", "scope of work", "sow", "risk summary", "cost estimate", "funding", "basis of design", "report", "memo"],
    "business_development": ["rfp", "solicitation", "contract award", "procurement", "bid", "grant", "sam.gov", "usaspending", "competitive", "proposal", "opportunity"],
}


def _classify_domain(query: str) -> str:
    q = query.lower()
    for domain, keywords in _DOMAIN_KEYWORDS.items():
        if any(kw in q for kw in keywords):
            return domain
    return "general"


# ─── MCP client factory ────────────────────────────────────────────────────────


def build_mcp_client() -> MultiServerMCPClient:
    mcp_url = os.environ.get(
        "MCP_SERVER_URL", "http://mcp-server.infra-advisor.svc.cluster.local:8000/mcp"
    )
    return MultiServerMCPClient(
        {
            "infratools": {
                "url": mcp_url,
                "transport": "streamable_http",
            }
        }
    )


# ─── LLM factory ──────────────────────────────────────────────────────────────


def build_llm(deployment: str | None = None) -> AzureChatOpenAI:
    dep = deployment or os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini")
    return AzureChatOpenAI(
        azure_deployment=dep,
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        # 2025-01-01-preview required for gpt-4.1 model family
        api_version="2025-01-01-preview",
        temperature=0,
        streaming=True,
    )


# ─── Router agent ──────────────────────────────────────────────────────────────


async def _run_router(query: str, llm: AzureChatOpenAI) -> _RouteDecision:
    """Route the query to a specialist agent.

    Uses LCEL chain with structured output — ddtrace auto-instruments
    BasePromptTemplate.ainvoke + chat_model.ainvoke inside this chain.
    Wrapped in LLMObs.agent("router") so it appears as a distinct node.
    """
    router_chain = _ROUTER_PROMPT | llm.with_structured_output(_RouteDecision)
    try:
        decision = await router_chain.ainvoke({"query": query})
        return decision
    except Exception as exc:
        logger.warning("router failed (non-fatal): %s", exc)
        return _RouteDecision(specialist="general", handoff_context=query)


# ─── Answer + tools extractor ──────────────────────────────────────────────────


def _extract_answer_and_tools(result: dict[str, Any]) -> tuple[str, list[str]]:
    """Extract final answer text and list of tool names from executor result."""
    all_messages = result.get("messages", [])

    answer = ""
    for msg in reversed(all_messages):
        if isinstance(msg, AIMessage) and msg.content:
            answer = msg.content if isinstance(msg.content, str) else str(msg.content)
            break

    tools_called: list[str] = []
    for msg in all_messages:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
                if name and name not in tools_called:
                    tools_called.append(name)

    return answer, tools_called


def _extract_sources_from_tool_content(content: Any) -> list[str]:
    """Pull `_source` fields out of a single tool result payload.

    Shared by run_agent's post-hoc extract-sources task (over all ToolMessages
    at once) and run_agent_stream's per-tool-call extraction (one payload at a
    time, as each on_tool_end event arrives) — same parsing rules, one place.
    """
    sources: list[str] = []
    try:
        if isinstance(content, list):
            items = content
        elif isinstance(content, str):
            parsed = json.loads(content)
            items = parsed if isinstance(parsed, list) else [parsed]
        else:
            items = []

        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text" and isinstance(item.get("text"), str):
                try:
                    inner = json.loads(item["text"])
                    records = inner if isinstance(inner, list) else [inner]
                except Exception:
                    records = []
                for record in records:
                    if isinstance(record, dict):
                        src = record.get("_source")
                        if src and src not in sources:
                            sources.append(src)
            else:
                src = item.get("_source")
                if src and src not in sources:
                    sources.append(src)
    except Exception:
        pass
    return sources


def _summarize_tool_result(raw: str) -> str | None:
    """Compact one-liner summary for a tool result chip — mirrors .NET's
    SummarizeToolResult in AgentService.cs: array length / object key count /
    error marker / char-or-KB count fallback for non-JSON payloads."""
    if not raw or not raw.strip():
        return None
    try:
        parsed = json.loads(raw)
    except Exception:
        length = len(raw)
        return f"{length} chars" if length < 1024 else f"{length // 1024} KB"

    if isinstance(parsed, list):
        return f"{len(parsed)} records"
    if isinstance(parsed, dict):
        if "error" in parsed:
            return "error"
        return f"{len(parsed)} fields"
    return None


# ─── Agent runner ──────────────────────────────────────────────────────────────


async def run_agent(
    query: str,
    session_id: str,
    mcp_client: MultiServerMCPClient,
    deployment: str,
    rum_session_id: str | None = None,
) -> dict[str, Any]:
    """Execute a multi-agent query pipeline with specialist routing.

    Trace structure visible in Datadog LLM Observability:
      workflow: query-processing
        task:   load-history
        agent:  router         → LCEL chain (BasePromptTemplate + chat_model auto-instrumented)
        agent:  specialist-{name} → ChatPromptTemplate.ainvoke + LangGraph executor
        task:   extract-sources
      task: faithfulness-eval (async, outside workflow)
        llm: (auto-instrumented OpenAI call inside eval)

    Specialists: transportation | water_energy | business_development | document | general
    Each specialist receives only the tools relevant to its domain.
    """
    # 0. AI Guard pre-flight check on the raw user query. Runs before
    #    anything else touches the LLM/tool loop — see observability/ai_guard.py.
    block_reason = check_query(query)
    if block_reason:
        logger.warning("AI Guard blocked query for session=%s: %s", session_id, block_reason)
        return {
            "answer": block_reason,
            "sources": [],
            "tools_called": [],
            "query_domain": "blocked",
        }

    llm = build_llm(deployment)
    query_domain = _classify_domain(query)
    all_tools = await mcp_client.get_tools()

    # session.id drives RUM↔LLM Obs correlation in Datadog — prefer the RUM session ID
    # when available so "View session replay" links work from LLM Obs traces.
    obs_session_id = rum_session_id or session_id

    with LLMObs.workflow("query-processing") as workflow_span:
        LLMObs.annotate(
            span=workflow_span,
            input_data={"content": query, "role": "user"},
            tags={
                "query.domain": query_domain,
                "session.id": obs_session_id,
                "session.chat_id": session_id,
                **({"session.rum_id": rum_session_id} if rum_session_id else {}),
            },
        )

        # ── Task: load session history ────────────────────────────────────────
        with LLMObs.task("load-history") as history_span:
            raw_history = load_history(session_id)
            LLMObs.annotate(
                span=history_span,
                tags={
                    "history.turns": str(len(raw_history)),
                    "session.id": obs_session_id,
                    "session.chat_id": session_id,
                },
            )

        # Build conversation history messages
        history_messages: list[Any] = []
        for entry in raw_history:
            role = entry.get("role", "")
            content = entry.get("content", "")
            if role == "human":
                history_messages.append(HumanMessage(content=content))
            elif role == "ai":
                history_messages.append(AIMessage(content=content))

        # ── Agent: router ─────────────────────────────────────────────────────
        with LLMObs.agent("router") as router_span:
            decision = await _run_router(query, llm)
            LLMObs.annotate(
                span=router_span,
                input_data={"content": query, "role": "user"},
                output_data={"content": decision.handoff_context, "role": "assistant"},
                tags={
                    "router.specialist": decision.specialist,
                    "router.handoff_context": decision.handoff_context[:200],
                    "query.domain": query_domain,
                    "session.id": obs_session_id,
                },
            )

        # ── Agent: specialist ─────────────────────────────────────────────────
        specialist_name = decision.specialist
        allowed_tools = _TOOL_PARTITIONS.get(specialist_name)

        # Filter tool set to specialist's partition (general gets all)
        if allowed_tools is not None:
            specialist_tools = [
                t for t in all_tools
                if (t.name if hasattr(t, "name") else str(t)) in allowed_tools
            ]
        else:
            specialist_tools = list(all_tools)

        system_prompt = _SPECIALIST_SYSTEM_PROMPTS[specialist_name]

        # Inject handoff context as a strategy hint when router added useful context
        if decision.handoff_context and decision.handoff_context != query:
            system_prompt = (
                f"{system_prompt}\n\n"
                f"[Routing context]: {decision.handoff_context}"
            )

        # Build the specialist prompt template — ddtrace auto-instruments
        # BasePromptTemplate.ainvoke() so this call produces a prompt span automatically
        specialist_prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            MessagesPlaceholder("messages"),
        ])

        # Assemble full message list: history + current query
        input_messages = history_messages + [HumanMessage(content=query)]

        executor = create_react_agent(
            model=llm,
            tools=specialist_tools,
            prompt=specialist_prompt,
        )

        with LLMObs.agent(f"specialist-{specialist_name}") as agent_span:
            # Explicitly invoke specialist_prompt so ddtrace captures the
            # BasePromptTemplate.ainvoke span with system prompt + messages
            formatted = await specialist_prompt.ainvoke({"messages": input_messages})
            result = await executor.ainvoke({"messages": input_messages})

            answer, tools_called = _extract_answer_and_tools(result)
            all_messages = result.get("messages", [])

            tag_agent_run(
                span=agent_span,
                query=query,
                answer=answer,
                query_domain=query_domain,
                tools_called=tools_called,
            )
            LLMObs.annotate(
                span=agent_span,
                tags={
                    "specialist": specialist_name,
                    "specialist.tools_available": str(len(specialist_tools)),
                    "session.id": obs_session_id,
                },
            )

        # ── Task: extract sources ─────────────────────────────────────────────
        sources: list[str] = []
        context_chunks: list[str] = []

        with LLMObs.task("extract-sources") as sources_span:
            for msg in all_messages:
                if isinstance(msg, ToolMessage):
                    context_chunks.append(str(msg.content)[:500])
                    for src in _extract_sources_from_tool_content(msg.content):
                        if src not in sources:
                            sources.append(src)

            LLMObs.annotate(
                span=sources_span,
                tags={
                    "sources.count": str(len(sources)),
                    "tools_called.count": str(len(tools_called)),
                    "context_chunks.count": str(len(context_chunks)),
                },
            )

        # Annotate workflow with final output
        LLMObs.annotate(
            span=workflow_span,
            output_data={"content": answer, "role": "assistant"},
            tags={
                "tools_called": ",".join(tools_called),
                "specialist": specialist_name,
            },
        )

    # Schedule faithfulness scoring outside the workflow — async, zero added latency
    schedule_faithfulness_score(
        query=query,
        context_chunks=context_chunks,
        answer=answer,
        session_id=obs_session_id,
        query_domain=query_domain,
    )

    return {
        "answer": answer,
        "sources": sources,
        "tools_called": tools_called,
        "query_domain": query_domain,
    }


# ─── Streaming agent runner ─────────────────────────────────────────────────────


async def run_agent_stream(
    query: str,
    session_id: str,
    mcp_client: MultiServerMCPClient,
    deployment: str,
    rum_session_id: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Streaming counterpart to run_agent — same router → specialist pipeline,
    but yields SSE-event dicts live as the specialist's LangGraph executor runs,
    using astream_events(version="v2") instead of a single ainvoke().

    Event dicts match services/agent-api-dotnet's StreamEvent contract exactly
    (snake_case keys the UI's parseSseBlock already expects):
      {"event": "step", "step", "status", "detail"}
      {"event": "tool_call_start", "id", "name", "args_json"}
      {"event": "tool_call_end", "id", "name", "status", "result_summary", "sources", "duration_ms"}
      {"event": "text_chunk", "chunk"}
      {"event": "done", "session_id", "model", "sources", "tools_called", "query_domain"}
      {"event": "error", "message", "category"}

    No retrieve_best_practices step — Python has no RetrievalService; that
    data lookup is just the search_project_knowledge MCP tool here, which
    surfaces as an ordinary tool_call_start/tool_call_end pair. No MCP
    session-expiry retry either — mcp_client.get_tools() is already called
    fresh every request (see below), unlike .NET's previously-cached client,
    so there's no matching stale-session failure mode to guard against.

    trace_id/span_id are intentionally omitted from the "done" event here —
    the caller (main.py's /query/stream handler) fills those in from its own
    current_trace_id()/current_span_id() call, same as the non-streaming
    /query handler does after run_agent() returns; agent.py has no other
    reason to depend on observability.tracing.
    """
    # 0. AI Guard pre-flight check on the raw user query — must run before
    #    the first "step" event goes out. Streaming can't rewind a step
    #    already shown as "running" in the UI, so a blocked query yields
    #    only "error" and returns, same as agent-api-dotnet's
    #    RunAgentStreamingAsync (AgentService.cs).
    block_reason = check_query(query)
    if block_reason:
        logger.warning("AI Guard blocked streaming query for session=%s: %s", session_id, block_reason)
        yield {"event": "error", "message": block_reason, "category": "blocked"}
        return

    llm = build_llm(deployment)
    query_domain = _classify_domain(query)
    obs_session_id = rum_session_id or session_id

    try:
        with LLMObs.workflow("query-processing") as workflow_span:
            LLMObs.annotate(
                span=workflow_span,
                input_data={"content": query, "role": "user"},
                tags={
                    "query.domain": query_domain,
                    "session.id": obs_session_id,
                    "session.chat_id": session_id,
                    **({"session.rum_id": rum_session_id} if rum_session_id else {}),
                },
            )

            yield {"event": "step", "step": "classify_domain", "status": "done", "detail": query_domain}

            with LLMObs.task("load-history") as history_span:
                raw_history = load_history(session_id)
                LLMObs.annotate(
                    span=history_span,
                    tags={
                        "history.turns": str(len(raw_history)),
                        "session.id": obs_session_id,
                        "session.chat_id": session_id,
                    },
                )

            history_messages: list[Any] = []
            for entry in raw_history:
                role = entry.get("role", "")
                content = entry.get("content", "")
                if role == "human":
                    history_messages.append(HumanMessage(content=content))
                elif role == "ai":
                    history_messages.append(AIMessage(content=content))

            yield {"event": "step", "step": "route_query", "status": "running", "detail": None}
            with LLMObs.agent("router") as router_span:
                decision = await _run_router(query, llm)
                LLMObs.annotate(
                    span=router_span,
                    input_data={"content": query, "role": "user"},
                    output_data={"content": decision.handoff_context, "role": "assistant"},
                    tags={
                        "router.specialist": decision.specialist,
                        "router.handoff_context": decision.handoff_context[:200],
                        "query.domain": query_domain,
                        "session.id": obs_session_id,
                    },
                )
            yield {
                "event": "step",
                "step": "route_query",
                "status": "done",
                "detail": decision.specialist,
            }

            specialist_name = decision.specialist
            all_tools = await mcp_client.get_tools()
            allowed_tools = _TOOL_PARTITIONS.get(specialist_name)
            if allowed_tools is not None:
                specialist_tools = [
                    t for t in all_tools
                    if (t.name if hasattr(t, "name") else str(t)) in allowed_tools
                ]
            else:
                specialist_tools = list(all_tools)

            system_prompt = _SPECIALIST_SYSTEM_PROMPTS[specialist_name]
            if decision.handoff_context and decision.handoff_context != query:
                system_prompt = f"{system_prompt}\n\n[Routing context]: {decision.handoff_context}"

            specialist_prompt = ChatPromptTemplate.from_messages([
                ("system", system_prompt),
                MessagesPlaceholder("messages"),
            ])
            input_messages = history_messages + [HumanMessage(content=query)]

            executor = create_react_agent(
                model=llm,
                tools=specialist_tools,
                prompt=specialist_prompt,
            )

            answer_parts: list[str] = []
            tools_called: list[str] = []
            sources: list[str] = []
            context_chunks: list[str] = []
            tool_names_by_run: dict[str, str] = {}
            tool_starts: dict[str, float] = {}

            with LLMObs.agent(f"specialist-{specialist_name}") as agent_span:
                # Explicitly invoke specialist_prompt so ddtrace captures the
                # BasePromptTemplate.ainvoke span, same as run_agent does.
                await specialist_prompt.ainvoke({"messages": input_messages})

                async for ev in executor.astream_events(
                    {"messages": input_messages}, version="v2"
                ):
                    kind = ev["event"]

                    if kind == "on_chat_model_stream":
                        chunk = ev["data"].get("chunk")
                        text = getattr(chunk, "content", None) if chunk is not None else None
                        if isinstance(text, str) and text:
                            answer_parts.append(text)
                            yield {"event": "text_chunk", "chunk": text}

                    elif kind == "on_tool_start":
                        run_id = str(ev.get("run_id", ""))
                        name = ev.get("name", "")
                        tool_names_by_run[run_id] = name
                        tool_starts[run_id] = time.monotonic()
                        if name and name not in tools_called:
                            tools_called.append(name)
                        tool_input = ev.get("data", {}).get("input")
                        yield {
                            "event": "tool_call_start",
                            "id": run_id,
                            "name": name,
                            "args_json": json.dumps(tool_input) if tool_input is not None else None,
                        }

                    elif kind in ("on_tool_end", "on_tool_error"):
                        run_id = str(ev.get("run_id", ""))
                        name = tool_names_by_run.get(run_id, ev.get("name", ""))
                        duration_ms = (time.monotonic() - tool_starts.get(run_id, time.monotonic())) * 1000

                        if kind == "on_tool_error":
                            result_content: Any = str(ev.get("data", {}).get("error", ""))
                            status = "error"
                        else:
                            output = ev.get("data", {}).get("output")
                            result_content = getattr(output, "content", output)
                            status = "error" if isinstance(result_content, str) and '"error"' in result_content else "ok"

                        context_chunks.append(str(result_content)[:500])
                        call_sources = _extract_sources_from_tool_content(result_content)
                        for src in call_sources:
                            if src not in sources:
                                sources.append(src)

                        result_str = result_content if isinstance(result_content, str) else json.dumps(result_content)
                        yield {
                            "event": "tool_call_end",
                            "id": run_id,
                            "name": name,
                            "status": status,
                            "result_summary": _summarize_tool_result(result_str),
                            "sources": call_sources,
                            "duration_ms": duration_ms,
                        }

                answer = "".join(answer_parts)
                tag_agent_run(
                    span=agent_span,
                    query=query,
                    answer=answer,
                    query_domain=query_domain,
                    tools_called=tools_called,
                )
                LLMObs.annotate(
                    span=agent_span,
                    tags={
                        "specialist": specialist_name,
                        "specialist.tools_available": str(len(specialist_tools)),
                        "session.id": obs_session_id,
                    },
                )

            LLMObs.annotate(
                span=workflow_span,
                output_data={"content": answer, "role": "assistant"},
                tags={
                    "tools_called": ",".join(tools_called),
                    "specialist": specialist_name,
                },
            )

        schedule_faithfulness_score(
            query=query,
            context_chunks=context_chunks,
            answer=answer,
            session_id=obs_session_id,
            query_domain=query_domain,
        )

        yield {
            "event": "done",
            "session_id": session_id,
            "model": deployment,
            "sources": sources,
            "tools_called": tools_called,
            "query_domain": query_domain,
        }

    except Exception as exc:
        logger.exception("run_agent_stream failed for session=%s", session_id)
        yield {
            "event": "error",
            "message": "The agent encountered an unexpected error. Please retry your question.",
            "category": "unknown",
        }
