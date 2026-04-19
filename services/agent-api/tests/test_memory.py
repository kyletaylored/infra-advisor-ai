"""Tests for session memory helpers (memory.py).

All Redis calls are patched — no live Redis required.
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Env + path setup
# ---------------------------------------------------------------------------

os.environ.setdefault("DD_AGENT_HOST", "localhost")
os.environ.setdefault("DD_TRACE_ENABLED", "false")
os.environ.setdefault("AZURE_OPENAI_ENDPOINT", "https://mock.openai.azure.com")
os.environ.setdefault("AZURE_OPENAI_API_KEY", "mock-key")

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from memory import (  # noqa: E402
    _WINDOW_SIZE,
    append_exchange,
    clear_session,
    history_to_langchain_messages,
    load_history,
    save_history,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_redis_mock(stored_value: str | None = None):
    """Return a mock redis.Redis instance."""
    mock = MagicMock()
    mock.get.return_value = stored_value
    mock.setex.return_value = True
    mock.delete.return_value = 1
    return mock


# ---------------------------------------------------------------------------
# load_history
# ---------------------------------------------------------------------------


def test_load_history_empty_when_key_missing():
    mock_redis = _make_redis_mock(stored_value=None)
    with patch("memory._redis_client", return_value=mock_redis):
        result = load_history("session-abc")
    assert result == []


def test_load_history_returns_parsed_messages():
    import json

    history = [
        {"role": "human", "content": "Hello"},
        {"role": "ai", "content": "Hi there"},
    ]
    mock_redis = _make_redis_mock(stored_value=json.dumps(history))
    with patch("memory._redis_client", return_value=mock_redis):
        result = load_history("session-abc")
    assert len(result) == 2
    assert result[0]["role"] == "human"
    assert result[1]["content"] == "Hi there"


def test_load_history_trims_to_window():
    import json

    # 12 exchange pairs = 24 messages; window is _WINDOW_SIZE pairs = 20 messages
    history = []
    for i in range(12):
        history.append({"role": "human", "content": f"Q{i}"})
        history.append({"role": "ai", "content": f"A{i}"})

    mock_redis = _make_redis_mock(stored_value=json.dumps(history))
    with patch("memory._redis_client", return_value=mock_redis):
        result = load_history("session-trim")

    assert len(result) <= _WINDOW_SIZE * 2


def test_load_history_returns_empty_on_redis_error():
    mock_redis = MagicMock()
    mock_redis.get.side_effect = Exception("connection refused")
    with patch("memory._redis_client", return_value=mock_redis):
        result = load_history("session-err")
    assert result == []


# ---------------------------------------------------------------------------
# save_history
# ---------------------------------------------------------------------------


def test_save_history_calls_setex_with_ttl():
    import json

    history = [{"role": "human", "content": "test"}]
    mock_redis = _make_redis_mock()
    with patch("memory._redis_client", return_value=mock_redis):
        save_history("session-save", history)

    mock_redis.setex.assert_called_once()
    call_args = mock_redis.setex.call_args
    key, ttl, value = call_args.args
    assert "session-save" in key
    assert ttl == 86_400
    stored = json.loads(value)
    assert stored[0]["role"] == "human"


def test_save_history_trims_before_saving():
    import json

    # 15 exchange pairs = 30 messages; window is 10 pairs = 20 messages
    history = []
    for i in range(15):
        history.append({"role": "human", "content": f"Q{i}"})
        history.append({"role": "ai", "content": f"A{i}"})

    mock_redis = _make_redis_mock()
    with patch("memory._redis_client", return_value=mock_redis):
        save_history("session-trim", history)

    call_args = mock_redis.setex.call_args
    stored = json.loads(call_args.args[2])
    assert len(stored) <= _WINDOW_SIZE * 2


# ---------------------------------------------------------------------------
# append_exchange
# ---------------------------------------------------------------------------


def test_append_exchange_adds_two_messages():
    import json

    existing = [{"role": "human", "content": "prior"}, {"role": "ai", "content": "prior-reply"}]
    mock_redis = _make_redis_mock(stored_value=json.dumps(existing))
    with patch("memory._redis_client", return_value=mock_redis):
        append_exchange("session-append", "new question", "new answer")

    call_args = mock_redis.setex.call_args
    stored = json.loads(call_args.args[2])
    # existing 2 + new 2 = 4
    assert len(stored) == 4
    assert stored[-2]["role"] == "human"
    assert stored[-2]["content"] == "new question"
    assert stored[-1]["role"] == "ai"
    assert stored[-1]["content"] == "new answer"


# ---------------------------------------------------------------------------
# clear_session
# ---------------------------------------------------------------------------


def test_clear_session_returns_true_when_key_deleted():
    mock_redis = _make_redis_mock()
    mock_redis.delete.return_value = 1
    with patch("memory._redis_client", return_value=mock_redis):
        result = clear_session("session-del")
    assert result is True


def test_clear_session_returns_false_when_key_missing():
    mock_redis = _make_redis_mock()
    mock_redis.delete.return_value = 0
    with patch("memory._redis_client", return_value=mock_redis):
        result = clear_session("session-no-exist")
    assert result is False


def test_clear_session_returns_false_on_redis_error():
    mock_redis = MagicMock()
    mock_redis.delete.side_effect = Exception("connection refused")
    with patch("memory._redis_client", return_value=mock_redis):
        result = clear_session("session-err")
    assert result is False


# ---------------------------------------------------------------------------
# history_to_langchain_messages
# ---------------------------------------------------------------------------


def test_history_to_langchain_messages_types():
    history = [
        {"role": "human", "content": "What bridges are structurally deficient?"},
        {"role": "ai", "content": "Here are the bridges..."},
    ]
    messages = history_to_langchain_messages(history)
    assert len(messages) == 2
    assert messages[0].__class__.__name__ == "HumanMessage"
    assert messages[1].__class__.__name__ == "AIMessage"
    assert messages[0].content == "What bridges are structurally deficient?"


def test_history_to_langchain_messages_empty():
    assert history_to_langchain_messages([]) == []


def test_history_to_langchain_messages_skips_unknown_role():
    history = [
        {"role": "system", "content": "ignored"},
        {"role": "human", "content": "hi"},
    ]
    messages = history_to_langchain_messages(history)
    # "system" role produces no message; only "human" does
    assert len(messages) == 1
    assert messages[0].__class__.__name__ == "HumanMessage"
