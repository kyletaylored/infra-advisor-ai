"""Tests for observability.tracing's log_external_api_failure / _redact.

Covers the fix for: external API failures (USASpending 422, SAM.gov 401,
malformed web-procurement JSON) previously had no response payload logged
or trace-tagged anywhere, making them undebuggable from Datadog.
"""

import os
import sys
from unittest.mock import MagicMock, patch

os.environ.setdefault("DD_AGENT_HOST", "localhost")
os.environ.setdefault("DD_TRACE_ENABLED", "false")
os.environ.setdefault("DD_DOGSTATSD_PORT", "8125")

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import logging

from observability.tracing import _redact, log_external_api_failure


# ---------------------------------------------------------------------------
# _redact
# ---------------------------------------------------------------------------


def test_redact_strips_api_key_from_url():
    url = "https://api.sam.gov/opportunities/v2/search?api_key=SECRET123&limit=25"
    redacted = _redact(url)
    assert "SECRET123" not in redacted
    assert "api_key=***" in redacted
    assert "limit=25" in redacted  # non-secret params untouched


def test_redact_strips_apikey_variant():
    url = "https://api.eia.gov/v2/electricity/data/?apikey=OTHERSECRET&frequency=annual"
    redacted = _redact(url)
    assert "OTHERSECRET" not in redacted
    assert "apikey=***" in redacted


def test_redact_applies_to_body_text_defensively():
    body = 'Error processing request for api_key=LEAKED_KEY_VALUE — invalid parameter'
    redacted = _redact(body)
    assert "LEAKED_KEY_VALUE" not in redacted
    assert "api_key=***" in redacted


def test_redact_is_case_insensitive():
    url = "https://example.com/?API_KEY=SECRET"
    redacted = _redact(url)
    assert "SECRET" not in redacted


def test_redact_noop_when_no_secret_present():
    url = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
    assert _redact(url) == url


# ---------------------------------------------------------------------------
# log_external_api_failure
# ---------------------------------------------------------------------------


def test_logs_warning_with_body_present():
    log = MagicMock(spec=logging.Logger)
    log_external_api_failure(
        log,
        source="usaspending",
        tool_name="get_contract_awards",
        status_code=422,
        body="Unprocessable Entity: invalid NAICS code",
    )
    log.warning.assert_called_once()
    call_args = log.warning.call_args[0]
    # First positional arg is the format string; the rest are %-args.
    assert 422 in call_args
    assert "Unprocessable Entity: invalid NAICS code" in call_args


def test_redacts_secret_before_logging():
    log = MagicMock(spec=logging.Logger)
    log_external_api_failure(
        log,
        source="samgov",
        tool_name="get_procurement_opportunities",
        status_code=401,
        body="Unauthorized",
        url="https://api.sam.gov/opportunities/v2/search?api_key=TOPSECRET&limit=25",
    )
    call_args = log.warning.call_args[0]
    assert not any("TOPSECRET" in str(a) for a in call_args)


def test_truncates_long_body():
    log = MagicMock(spec=logging.Logger)
    long_body = "x" * 5000
    log_external_api_failure(
        log, source="eia", tool_name="get_energy_infrastructure", body=long_body
    )
    call_args = log.warning.call_args[0]
    logged_body = call_args[-1]
    assert len(logged_body) <= 2000


def test_accepts_error_string_for_sdk_mediated_failures():
    """project_knowledge.py / water_infrastructure.py's Azure SDK paths have no
    raw HTTP response — only an exception string."""
    log = MagicMock(spec=logging.Logger)
    log_external_api_failure(
        log,
        source="azure_ai_search",
        tool_name="search_project_knowledge",
        error="Index 'infra-advisor-knowledge' not found",
    )
    log.warning.assert_called_once()
    call_args = log.warning.call_args[0]
    assert "Index 'infra-advisor-knowledge' not found" in call_args


def test_tags_active_span_when_present():
    log = MagicMock(spec=logging.Logger)
    with patch("observability.tracing.tag_span") as mock_tag_span:
        log_external_api_failure(
            log,
            source="usaspending",
            tool_name="get_contract_awards",
            status_code=422,
            body="Unprocessable Entity",
        )
    tagged = {call.args[0]: call.args[1] for call in mock_tag_span.call_args_list}
    assert tagged["error.source"] == "usaspending"
    assert tagged["error.tool"] == "get_contract_awards"
    assert tagged["error.status_code"] == 422
    assert "Unprocessable Entity" in tagged["error.response_body"]


def test_no_op_span_tagging_when_no_active_span():
    """tag_span itself is a no-op when tracer.current_span() is None — confirm
    log_external_api_failure doesn't raise even with no active trace."""
    log = MagicMock(spec=logging.Logger)
    log_external_api_failure(
        log, source="usaspending", tool_name="get_contract_awards", status_code=500
    )
    log.warning.assert_called_once()
