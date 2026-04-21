import ddtrace.auto  # must be first import — auto-instruments LangChain, LangGraph, OpenAI, MCP, httpx, Redis, Kafka

import json
import logging
import os
from typing import Any, Literal

from ddtrace.llmobs import LLMObs
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_openai import AzureChatOpenAI
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel

from memory import load_history
from observability.llm_obs import schedule_faithfulness_score, tag_agent_run

logger = logging.getLogger(__name__)

# ─── Routing model ─────────────────────────────────────────────────────────────


class _RouteDecision(BaseModel):
    specialist: Literal["transportation", "water_energy", "business_development", "document", "general"]
    handoff_context: str


# ─── Tool partitions ───────────────────────────────────────────────────────────

_TOOL_PARTITIONS: dict[str, list[str] | None] = {
    "transportation": [
        "get_bridge_condition",
        "get_disaster_history",
        "search_txdot_open_data",
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
    "transportation": """You are InfraAdvisor Transportation Specialist, an expert in transportation \
infrastructure analysis for a global infrastructure consulting firm.

Your focus: bridges (FHWA NBI), highways, rail, traffic data (TxDOT AADT), disaster impacts on \
transportation networks, and transportation document drafting.

Guidelines:
1. Always cite NBI structure numbers, TxDOT dataset IDs, or FEMA declaration IDs
2. Sort bridge lists by ascending sufficiency rating (lowest = highest risk first)
3. Flag scour vulnerability, load rating deficiencies, and fracture-critical status explicitly
4. Combine NBI data with disaster history to assess flood/scour compounding risk
5. For document drafts, call search_project_knowledge first for relevant templates
6. Do not speculate about conditions not in the data — say "not available in the dataset"
7. Keep factual lookups concise; detailed for document drafts""",

    "water_energy": """You are InfraAdvisor Water & Energy Specialist, an expert in water systems \
and energy infrastructure analysis for a global infrastructure consulting firm.

Your focus: public water systems (EPA SDWIS compliance), TWDB 2026 State Water Plan projects, \
EIA energy generation data, ERCOT Texas grid storage (ESR), and environmental/utility document drafting.

Guidelines:
1. Always cite PWSID, TWDB project IDs, EIA plant IDs, or ERCOT ESR resource IDs
2. Sort water systems by descending violation count (most violations = highest risk first)
3. Flag open Safe Drinking Water Act violations, boil-water notices, and unresolved enforcement actions
4. For water queries, combine get_water_infrastructure (compliance) with search_project_knowledge (firm history)
5. For ERCOT queries, note grid stress periods and storage discharge patterns
6. For document drafts, call search_project_knowledge first for relevant templates
7. Do not speculate about conditions not in the data — say "not available in the dataset"
8. Keep factual lookups concise; detailed for document drafts""",

    "business_development": """You are InfraAdvisor Business Development Specialist, an expert in \
federal procurement intelligence and market positioning for a global infrastructure consulting firm.

Your focus: federal contract awards (USASpending.gov), active federal opportunities (SAM.gov, grants.gov), \
state/local RFPs and bond elections (web procurement search), and competitive landscape analysis.

Guidelines:
1. Always call get_contract_awards BEFORE get_procurement_opportunities — understanding who won \
similar work informs positioning for open opportunities
2. Cite USASpending award IDs, SAM.gov solicitation numbers, grants.gov opportunity IDs
3. For web procurement results, always note the confidence field and flag medium-confidence \
extractions explicitly so users can verify before acting
4. Identify incumbent contractors, pricing benchmarks, and agency spending patterns from awards data
5. Match NAICS codes to infrastructure domains: 237110 (water), 237310 (highway), 237990 (other heavy)
6. Flag grant deadlines and application windows prominently
7. Keep competitive intelligence summaries actionable — focus on win themes and differentiators""",

    "document": """You are InfraAdvisor Document Specialist, an expert in drafting infrastructure \
consulting deliverables for a global infrastructure consulting firm.

Your focus: Scopes of Work (SOW), risk summaries, cost estimates, funding memos, and technical reports \
across transportation, water, energy, and environmental domains.

Guidelines:
1. Always call search_project_knowledge FIRST to retrieve relevant templates and prior project context
2. Structure documents with clear sections: executive summary, scope, methodology, deliverables, timeline
3. For risk summaries, query asset condition data to ground the document in actual findings
4. Cite data sources used to populate factual sections (NBI numbers, PWSID, EIA IDs)
5. Flag where client-specific data placeholders need to be filled in
6. Keep cost estimates clearly marked as order-of-magnitude unless detailed scope supports more precision
7. Match document tone to audience: technical for engineering reviewers, executive for leadership""",

    "general": """You are InfraAdvisor, a technical AI assistant for infrastructure consultants and \
solutions architects at a global infrastructure consulting firm.

Your expertise covers transportation infrastructure (bridges, highways, rail), \
water systems, energy infrastructure, environmental engineering, and construction \
management across the full project lifecycle from advisory to delivery.

You have access to tools covering bridges (NBI), disasters (FEMA), energy (EIA/ERCOT), \
water systems (EPA SDWIS/TWDB), Texas transportation (TxDOT), internal knowledge base, \
document drafting, and federal procurement intelligence (SAM.gov, USASpending.gov, web search).

Guidelines:
1. Always cite the data source for factual claims
2. Sort assets by risk or priority (lowest sufficiency rating first for bridges; highest violations for water)
3. Flag material risks explicitly — scour vulnerability, age, load rating issues, repeat flood events, violations
4. For water infrastructure queries, combine get_water_infrastructure with search_project_knowledge
5. For draft documents, call search_project_knowledge first
6. Do not speculate about asset conditions not in the data
7. Respond in the same language the user writes in
8. Keep responses concise for factual lookups; detailed for document drafts
9. For business development queries, always call get_contract_awards before get_procurement_opportunities
10. When search_web_procurement returns results, flag medium-confidence extractions explicitly""",
}

# ─── Router prompt ─────────────────────────────────────────────────────────────

_ROUTER_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a routing assistant for an infrastructure consulting AI system. "
        "Given a user query, select the most appropriate specialist agent to handle it:\n\n"
        "- transportation: bridges, highways, rail, traffic data, TxDOT, NBI, disaster impacts on roads\n"
        "- water_energy: water systems (SDWIS/TWDB), energy generation (EIA), ERCOT Texas grid, utilities\n"
        "- business_development: federal contracts, procurement opportunities, SAM.gov, RFPs, competitive intelligence\n"
        "- document: drafting SOWs, risk summaries, cost estimates, technical reports, funding memos\n"
        "- general: multi-domain queries, unclear scope, or queries spanning more than two domains\n\n"
        "Also provide a brief handoff_context (1-2 sentences) summarizing the key focus for the specialist.",
    ),
    ("human", "{query}"),
])

# ─── Domain classifier (kept for tags/metadata) ────────────────────────────────

_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "transportation": ["bridge", "highway", "rail", "nbi", "aadt", "sufficiency", "txdot", "traffic", "construction"],
    "water": ["water", "sdwis", "twdb", "pwsid", "violation", "desalination", "aquifer"],
    "energy": ["energy", "eia", "grid", "generation", "fuel", "solar", "wind", "ercot", "storage", "esr"],
    "document": ["draft", "scope of work", "sow", "risk summary", "cost estimate", "funding"],
    "business_development": ["rfp", "solicitation", "contract award", "procurement", "bid", "grant", "sam.gov", "usaspending", "competitive", "proposal"],
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

                    try:
                        content = msg.content
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
