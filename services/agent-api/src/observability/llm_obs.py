"""LLM Observability helpers for agent-api.

Provides:
- enable_llm_obs() — called once at startup
- tag_agent_run() — attach query-level tags to the current LLMObs span
- async_faithfulness_score() — fire-and-forget faithfulness evaluation
"""

import asyncio
import logging
import os

from ddtrace.llmobs import LLMObs

logger = logging.getLogger(__name__)

# DogStatsD client for faithfulness gauge
try:
    from ddtrace.internal.dogstatsd import get_dogstatsd_client as _get_statsd

    statsd = _get_statsd(
        f"{os.environ.get('DD_AGENT_HOST', 'localhost')}:"
        f"{os.environ.get('DD_DOGSTATSD_PORT', '8125')}"
    )
except Exception:  # pragma: no cover
    statsd = None  # type: ignore


def enable_llm_obs() -> None:
    """Enable Datadog LLM Observability.  Called once during FastAPI lifespan."""
    ml_app = os.environ.get("DD_LLMOBS_ML_APP", "infra-advisor-ai")
    agentless = os.environ.get("DD_LLMOBS_AGENTLESS_ENABLED", "false").lower() == "true"
    try:
        LLMObs.enable(ml_app=ml_app, agentless_enabled=agentless)
        logger.info("LLMObs enabled ml_app=%s agentless=%s", ml_app, agentless)
    except Exception as exc:  # pragma: no cover
        logger.warning("LLMObs.enable() failed (non-fatal): %s", exc)


def tag_agent_run(
    query_domain: str,
    tools_called: list[str],
    cost_usd: float | None = None,
) -> None:
    """Attach tags to the current active LLMObs span."""
    try:
        span = LLMObs.current_span()
        if span is None:
            return
        LLMObs.annotate(
            span=span,
            tags={
                "query.domain": query_domain,
                "agent.tools_called": ",".join(tools_called),
            },
        )
        if cost_usd is not None:
            LLMObs.annotate(span=span, tags={"llm.cost_usd": str(cost_usd)})
    except Exception as exc:  # pragma: no cover
        logger.debug("tag_agent_run failed (non-fatal): %s", exc)


async def _compute_faithfulness(
    query: str,
    context_chunks: list[str],
    answer: str,
    session_id: str,
    query_domain: str,
) -> None:
    """
    Compute a faithfulness score via a lightweight Azure OpenAI call and emit
    it as a DogStatsD gauge.  Runs as a background task — failures are logged,
    never raised.
    """
    try:
        from openai import AsyncAzureOpenAI

        client = AsyncAzureOpenAI(
            azure_endpoint=os.environ.get("AZURE_OPENAI_ENDPOINT", ""),
            api_key=os.environ.get("AZURE_OPENAI_API_KEY", ""),
            api_version="2024-02-01",
        )

        context_text = "\n---\n".join(context_chunks[:5]) if context_chunks else "(no context)"
        eval_prompt = (
            f"Context:\n{context_text}\n\n"
            f"Question: {query}\n\n"
            f"Answer: {answer}\n\n"
            "Rate how well the answer is grounded in the provided context.\n"
            "Reply with only a number between 0.0 (completely ungrounded) and "
            "1.0 (fully grounded). No explanation."
        )

        response = await client.chat.completions.create(
            model=os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o"),
            messages=[{"role": "user", "content": eval_prompt}],
            temperature=0,
            max_tokens=5,
        )
        raw = response.choices[0].message.content or ""
        score = float(raw.strip())
        score = max(0.0, min(1.0, score))

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


def schedule_faithfulness_score(
    query: str,
    context_chunks: list[str],
    answer: str,
    session_id: str,
    query_domain: str = "general",
) -> None:
    """
    Fire-and-forget wrapper for faithfulness scoring.
    Creates an asyncio Task; safe to call from a running event loop.
    """
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(
            _compute_faithfulness(query, context_chunks, answer, session_id, query_domain)
        )
    except Exception as exc:  # pragma: no cover
        logger.debug("schedule_faithfulness_score failed to schedule task: %s", exc)
