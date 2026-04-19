"""Redis-backed session memory for the InfraAdvisor agent.

Key pattern : infra-advisor:session:{session_id}:memory
Window      : last 10 human/AI exchange pairs
TTL         : 86400 seconds (24 hours), refreshed on every write
"""

import json
import logging
import os
from typing import Any

import redis

logger = logging.getLogger(__name__)

_SESSION_PREFIX = "infra-advisor:session"
_MEMORY_SUFFIX = "memory"
_SESSION_TTL = 86_400  # 24 hours
_WINDOW_SIZE = 10  # exchange pairs to retain


def _redis_client() -> redis.Redis:
    host = os.environ.get("REDIS_HOST", "redis.infra-advisor.svc.cluster.local")
    port = int(os.environ.get("REDIS_PORT", "6379"))
    return redis.Redis(host=host, port=port, decode_responses=True)


def _memory_key(session_id: str) -> str:
    return f"{_SESSION_PREFIX}:{session_id}:{_MEMORY_SUFFIX}"


def load_history(session_id: str) -> list[dict[str, str]]:
    """Return the conversation history list for this session.

    Each entry is ``{"role": "human"|"ai", "content": "..."}``
    Returns an empty list if the session does not exist or Redis is unavailable.
    """
    key = _memory_key(session_id)
    try:
        client = _redis_client()
        raw = client.get(key)
        if raw is None:
            return []
        history: list[dict[str, str]] = json.loads(raw)
        return history[-_WINDOW_SIZE * 2 :]  # keep last N pairs (2 messages per pair)
    except Exception as exc:
        logger.warning("load_history failed for session=%s: %s", session_id, exc)
        return []


def save_history(session_id: str, history: list[dict[str, str]]) -> None:
    """Persist the conversation history and refresh TTL.

    Truncates to the last ``_WINDOW_SIZE`` exchange pairs before saving.
    """
    key = _memory_key(session_id)
    # Keep last N pairs (2 messages per pair: human + ai)
    trimmed = history[-_WINDOW_SIZE * 2 :]
    try:
        client = _redis_client()
        client.setex(key, _SESSION_TTL, json.dumps(trimmed))
    except Exception as exc:
        logger.warning("save_history failed for session=%s: %s", session_id, exc)


def append_exchange(session_id: str, human_message: str, ai_message: str) -> None:
    """Append a human/AI exchange to the session history and refresh TTL."""
    history = load_history(session_id)
    history.append({"role": "human", "content": human_message})
    history.append({"role": "ai", "content": ai_message})
    save_history(session_id, history)


def clear_session(session_id: str) -> bool:
    """Delete session memory from Redis.  Returns True if key was deleted."""
    key = _memory_key(session_id)
    try:
        client = _redis_client()
        deleted = client.delete(key)
        return bool(deleted)
    except Exception as exc:
        logger.warning("clear_session failed for session=%s: %s", session_id, exc)
        return False


def history_to_langchain_messages(history: list[dict[str, str]]) -> list[Any]:
    """Convert stored history to LangChain HumanMessage/AIMessage objects."""
    from langchain_core.messages import AIMessage, HumanMessage

    messages: list[Any] = []
    for entry in history:
        role = entry.get("role", "")
        content = entry.get("content", "")
        if role == "human":
            messages.append(HumanMessage(content=content))
        elif role == "ai":
            messages.append(AIMessage(content=content))
    return messages
