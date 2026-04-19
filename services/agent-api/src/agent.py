import ddtrace.auto  # must be first import — monkey-patches openai, langchain at import time

import logging
import os
from typing import Any

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_openai import AzureChatOpenAI

from memory import load_history
from observability.llm_obs import schedule_faithfulness_score, tag_agent_run

logger = logging.getLogger(__name__)

# ─── System prompt ─────────────────────────────────────────────────────────────

_SYSTEM_PROMPT_TEXT = """You are InfraAdvisor, a technical AI assistant for infrastructure consultants and \
solutions architects at a global infrastructure consulting firm.

Your expertise covers transportation infrastructure (bridges, highways, rail), \
water systems, energy infrastructure, environmental engineering, and construction \
management across the full project lifecycle from advisory to delivery.

You have access to the following tools:
- get_bridge_condition: Query the FHWA National Bridge Inventory
- get_disaster_history: Query FEMA disaster declarations and public assistance data
- get_energy_infrastructure: Query EIA energy generation and infrastructure data
- get_water_infrastructure: Query EPA SDWIS for public water system compliance data and TWDB 2026 State Water Plan projects
- search_project_knowledge: Search the firm's internal knowledge base
- draft_document: Generate structured document scaffolds (SOW, risk summaries, cost estimates)

Guidelines:
1. Always cite the data source for factual claims (NBI structure number, FEMA declaration ID, PWSID, TWDB project ID, etc.)
2. When asked for a list of assets, always sort by risk or priority (lowest sufficiency rating first for bridges; highest violation count first for water systems)
3. Flag material risks explicitly — scour vulnerability, age, load rating issues, repeat flood events, open Safe Drinking Water Act violations
4. For water infrastructure queries, combine get_water_infrastructure (structured compliance/project data) with search_project_knowledge (firm history) to give both regulatory context and relevant internal experience
5. For draft documents, call search_project_knowledge first to retrieve relevant templates and context
6. Do not speculate about asset conditions not in the data — say "not available in the dataset"
7. Respond in the same language the user writes in
8. Keep responses concise for factual lookups; detailed for document drafts"""


# ─── Domain classifier ─────────────────────────────────────────────────────────

_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "transportation": ["bridge", "highway", "rail", "nbi", "aadt", "sufficiency"],
    "water": ["water", "sdwis", "twdb", "pwsid", "violation", "desalination", "aquifer"],
    "energy": ["energy", "eia", "grid", "generation", "fuel", "solar", "wind"],
    "document": ["draft", "scope of work", "sow", "risk summary", "cost estimate", "funding"],
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


def build_llm() -> AzureChatOpenAI:
    return AzureChatOpenAI(
        azure_deployment=os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o"),
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        api_version="2024-02-01",
        temperature=0,
        streaming=True,
    )


# ─── Agent runner ──────────────────────────────────────────────────────────────


async def run_agent(
    query: str,
    session_id: str,
    mcp_client: MultiServerMCPClient,
    llm: AzureChatOpenAI,
) -> dict[str, Any]:
    """
    Execute a single agent query.

    Returns:
        {
            "answer": str,
            "sources": list[str],
            "tools_called": list[str],
            "query_domain": str,
        }
    """
    query_domain = _classify_domain(query)

    # Load session history from Redis
    raw_history = load_history(session_id)

    # Build message list: system + history + current query
    messages: list[Any] = [SystemMessage(content=_SYSTEM_PROMPT_TEXT)]
    for entry in raw_history:
        role = entry.get("role", "")
        content = entry.get("content", "")
        if role == "human":
            messages.append(HumanMessage(content=content))
        elif role == "ai":
            messages.append(AIMessage(content=content))
    messages.append(HumanMessage(content=query))

    # Build tools and agent (lazy per-request so tool list stays fresh)
    tools = await mcp_client.get_tools()
    agent = create_agent(model=llm, tools=tools, system_prompt=_SYSTEM_PROMPT_TEXT)

    # Invoke — new API takes {"messages": [...]}
    result = await agent.ainvoke({"messages": messages})

    # Extract final answer from last AI message
    answer = ""
    all_messages = result.get("messages", [])
    for msg in reversed(all_messages):
        if isinstance(msg, AIMessage) and msg.content:
            answer = msg.content if isinstance(msg.content, str) else str(msg.content)
            break

    # Extract tool names and source citations from tool messages
    tools_called: list[str] = []
    sources: list[str] = []
    context_chunks: list[str] = []

    from langchain_core.messages import ToolMessage

    for msg in all_messages:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
                if name and name not in tools_called:
                    tools_called.append(name)

        if isinstance(msg, ToolMessage):
            content_str = str(msg.content)
            context_chunks.append(content_str[:500])

            # Try to parse _source fields from JSON-like observation
            try:
                import json

                parsed = json.loads(msg.content)
                items = parsed if isinstance(parsed, list) else [parsed]
                for item in items:
                    if isinstance(item, dict):
                        src = item.get("_source")
                        if src and src not in sources:
                            sources.append(src)
            except Exception:
                pass

    # Tag LLMObs span
    tag_agent_run(
        query_domain=query_domain,
        tools_called=tools_called,
    )

    # Schedule async faithfulness scoring (non-blocking)
    schedule_faithfulness_score(
        query=query,
        context_chunks=context_chunks,
        answer=answer,
        session_id=session_id,
        query_domain=query_domain,
    )

    return {
        "answer": answer,
        "sources": sources,
        "tools_called": tools_called,
        "query_domain": query_domain,
    }
