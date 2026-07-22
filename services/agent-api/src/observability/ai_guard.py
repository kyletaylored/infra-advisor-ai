"""Explicit AI Guard pre-flight check on the raw user query.

Mirrors agent-api-dotnet's DatadogAiGuardClient.EvaluateAsync: run before
anything else touches the LLM/tool loop, so a blocked query never starts a
"classify_domain"/"route_query" step the UI would otherwise show as stuck
"running" forever (there'd be no matching "done" event once the pipeline
aborts). ddtrace's own automatic LangChain integration still evaluates
every downstream chat-model call as a second layer — this module only
covers the up-front check needed for a clean UX on the common case.

Fails open (returns None) when AI Guard is disabled or the evaluate call
itself errors — a Datadog outage or misconfiguration must never block
legitimate traffic. Only an explicit DENY/ABORT from a successful
evaluation blocks.
"""

import logging
import os

from ddtrace.appsec.ai_guard import (
    AIGuardAbortError,
    AIGuardClientError,
    Message,
    Options,
    new_ai_guard_client,
)

logger = logging.getLogger(__name__)

_enabled = os.environ.get("DD_AI_GUARD_ENABLED", "").lower() == "true"
_client = None
if _enabled:
    try:
        _client = new_ai_guard_client()
    except Exception:
        logger.warning(
            "AI Guard client init failed; pre-flight check disabled (fail open)",
            exc_info=True,
        )


def check_query(query: str) -> str | None:
    """Return a block reason if the query should be blocked, else None."""
    if _client is None:
        return None

    try:
        _client.evaluate([Message(role="user", content=query)], Options(block=True))
        return None
    except AIGuardAbortError as exc:
        logger.warning("AI Guard blocked query: %s — %s", exc.action, exc.reason)
        return exc.reason or f"Blocked by AI Guard ({exc.action})"
    except AIGuardClientError as exc:
        logger.warning("AI Guard evaluate call failed: %s", exc)
        return None
