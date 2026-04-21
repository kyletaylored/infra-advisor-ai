"""LLM Observability helpers for agent-api.

Instrumentation strategy
------------------------
- LangChain chain/llm/tool calls  → auto-instrumented by ddtrace LangChain integration (>=2.9)
- MCP ClientSession.call_tool      → auto-instrumented by ddtrace MCP integration (>=3.11)
- Azure OpenAI chat completions    → auto-instrumented by ddtrace OpenAI integration (>=2.9)
- Agent-level span                 → explicit LLMObs.agent() in agent.py (owns annotations)
- Faithfulness eval LLM call      → explicit LLMObs.llm() below (separate eval sub-span)
"""

import asyncio
import logging
import os
from typing import Any

from ddtrace.llmobs import LLMObs

logger = logging.getLogger(__name__)

# DogStatsD client for faithfulness gauge metric
try:
    from ddtrace.internal.dogstatsd import get_dogstatsd_client as _get_statsd

    statsd = _get_statsd(
        f"{os.environ.get('DD_AGENT_HOST', 'localhost')}:"
        f"{os.environ.get('DD_DOGSTATSD_PORT', '8125')}"
    )
except Exception:  # pragma: no cover
    statsd = None  # type: ignore

# ── Faithfulness evaluator system prompt ─────────────────────────────────────
# Kept separate so ddtrace sees a proper system role in the LLM call,
# not a user message that bundles instructions + context.
_EVAL_SYSTEM_PROMPT = (
    "You are a faithfulness evaluator. "
    "Given a context passage, a question, and an answer, rate how well "
    "the answer is grounded in the provided context. "
    "Reply with ONLY a decimal number between 0.0 (completely ungrounded) "
    "and 1.0 (fully grounded). No explanation, no extra text."
)


def enable_llm_obs() -> None:
    """Enable Datadog LLM Observability.

    Called once during FastAPI lifespan startup.  If DD_LLMOBS_ENABLED=true is
    already set in the environment (and ddtrace.auto was the first import),
    this is a no-op — LLMObs is already active.
    """
    ml_app = os.environ.get("DD_LLMOBS_ML_APP", "infra-advisor-ai")
    agentless = os.environ.get("DD_LLMOBS_AGENTLESS_ENABLED", "false").lower() == "true"
    try:
        LLMObs.enable(ml_app=ml_app, agentless_enabled=agentless)
        logger.info("LLMObs enabled ml_app=%s agentless=%s", ml_app, agentless)
    except Exception as exc:  # pragma: no cover
        logger.warning("LLMObs.enable() failed (non-fatal): %s", exc)


def tag_agent_run(
    span: Any,
    query: str,
    answer: str,
    query_domain: str,
    tools_called: list[str],
    cost_usd: float | None = None,
) -> None:
    """Annotate an explicit LLMObs agent span with I/O and query-level tags.

    Must be called while the LLMObs.agent() context manager is still open
    so that span is the active span — not after ainvoke() returns.
    """
    try:
        LLMObs.annotate(
            span=span,
            input_data={"content": query, "role": "user"},
            output_data={"content": answer, "role": "assistant"},
            tags={
                "query.domain": query_domain,
                "agent.tools_called": ",".join(tools_called),
                **({"llm.cost_usd": str(cost_usd)} if cost_usd is not None else {}),
            },
        )
    except Exception as exc:  # pragma: no cover
        logger.debug("tag_agent_run failed (non-fatal): %s", exc)


async def _compute_faithfulness(
    query: str,
    context_chunks: list[str],
    answer: str,
    session_id: str,
    query_domain: str,
) -> None:
    """Faithfulness eval via gpt-4.1-mini.

    Runs as a background task (fire-and-forget) — zero added latency for users.
    Uses an explicit LLMObs.llm() span so the eval call appears as a separate
    sub-trace in LLM Observability, not bundled into the main agent span.

    The system/user message split ensures ddtrace classifies roles correctly:
    - system: evaluator instructions
    - user:   context + question + answer (the data to evaluate)
    """
    try:
        from openai import AsyncAzureOpenAI

        client = AsyncAzureOpenAI(
            azure_endpoint=os.environ.get("AZURE_OPENAI_ENDPOINT", ""),
            api_key=os.environ.get("AZURE_OPENAI_API_KEY", ""),
            api_version="2025-01-01-preview",
        )

        eval_model = os.environ.get("AZURE_OPENAI_EVAL_DEPLOYMENT", "gpt-4.1-mini")

        context_text = "\n---\n".join(context_chunks[:5]) if context_chunks else "(no context)"
        user_content = (
            f"Context:\n{context_text}\n\n"
            f"Question: {query}\n\n"
            f"Answer: {answer}"
        )

        # Explicit LLMObs.llm() span — shows eval call separately in LLM Obs UI,
        # tagged with the nano model so it's distinct from the main agent call.
        with LLMObs.llm(model_name=eval_model, model_provider="azure_openai") as eval_span:
            response = await client.chat.completions.create(
                model=eval_model,
                messages=[
                    {"role": "system", "content": _EVAL_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                temperature=0,
                max_tokens=5,
            )

            raw = response.choices[0].message.content or ""
            score = max(0.0, min(1.0, float(raw.strip())))

            usage = response.usage
            LLMObs.annotate(
                span=eval_span,
                input_data=[
                    {"role": "system", "content": _EVAL_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                output_data={"role": "assistant", "content": raw},
                tags={
                    "session.id": session_id,
                    "query.domain": query_domain,
                    "eval.faithfulness_score": str(score),
                },
                metrics={
                    "input_tokens": usage.prompt_tokens if usage else 0,
                    "output_tokens": usage.completion_tokens if usage else 0,
                },
            )

        logger.info(
            "faithfulness_score=%.3f session_id=%s domain=%s",
            score,
            session_id,
            query_domain,
        )

        if statsd is not None:
            statsd.gauge(
                "eval.faithfulness_score",
                score,
                tags=[f"session_id:{session_id}", f"query.domain:{query_domain}"],
            )

    except Exception as exc:
        logger.warning("faithfulness scoring failed (non-fatal): %s", exc)


def submit_user_feedback(
    trace_id: str,
    span_id: str,
    rating: str,
    session_id: str | None = None,
) -> None:
    """Submit a user feedback evaluation to Datadog LLM Observability.

    Attaches a categorical `user_feedback` evaluation to the LLM span identified
    by the given trace/span IDs.  `rating` must be one of: positive, negative, reported.
    """
    try:
        tags: dict[str, str] = {}
        if session_id:
            tags["session.id"] = session_id

        LLMObs.submit_evaluation(
            span_context={"trace_id": trace_id, "span_id": span_id},
            label="user_feedback",
            metric_type="categorical",
            value=rating,
            tags=tags,
        )
        logger.info("user_feedback submitted trace_id=%s rating=%s", trace_id, rating)
    except Exception as exc:
        logger.warning("submit_user_feedback failed (non-fatal): %s", exc)


def schedule_faithfulness_score(
    query: str,
    context_chunks: list[str],
    answer: str,
    session_id: str,
    query_domain: str = "general",
) -> None:
    """Fire-and-forget wrapper for faithfulness scoring."""
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(
            _compute_faithfulness(query, context_chunks, answer, session_id, query_domain)
        )
    except Exception as exc:  # pragma: no cover
        logger.debug("schedule_faithfulness_score failed to schedule task: %s", exc)
