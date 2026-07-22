import ddtrace.auto  # must be first import

import logging
import re

from ddtrace import tracer


def current_trace_id() -> str | None:
    """Return the current Datadog trace ID as a hex string, or None if no active span."""
    span = tracer.current_span()
    if span is None:
        return None
    return format(span.trace_id, "032x")


def tag_span(key: str, value: str | int | float) -> None:
    """Set a tag on the current active span, if one exists."""
    span = tracer.current_span()
    if span is not None:
        span.set_tag(key, value)


# ---------------------------------------------------------------------------
# External API failure logging — response payload capture
# ---------------------------------------------------------------------------
#
# Every tool's httpx error branch previously called emit_external_api(...) for
# metrics only, with no log line or span tag carrying the actual response
# body — a failure like "USASpending returned HTTP 422" was unrecoverable
# from Datadog beyond the bare status code. logger.warning(...) is already
# trace-correlated for free (DD_LOGS_INJECTION=true, k8s/mcp-server/
# configmap.yaml), so logging the body is enough to get it into Trace > Logs;
# tag_span additionally puts it directly on the span so it's visible in APM
# without a Logs pivot.

_MAX_BODY_CHARS = 2000
# EIA and SAM.gov both pass their key as ?api_key=... — the only secret that
# can appear in a URL across these tools (headers are never logged, so
# header-based secrets like ERCOT's Ocp-Apim-Subscription-Key never reach
# this code path).
_SECRET_PARAM_RE = re.compile(r"(?i)\b(api_key|apikey)=[^&\s\"']+")


def _redact(text: str) -> str:
    """Replace known secret query-param values with a placeholder. Applied to
    both URLs and body text defensively, in case a response ever echoes
    request params back."""
    return _SECRET_PARAM_RE.sub(r"\1=***", text)


def log_external_api_failure(
    log: logging.Logger,
    *,
    source: str,
    tool_name: str,
    status_code: int | None = None,
    body: str | bytes | None = None,
    url: str | None = None,
    error: str | None = None,
) -> None:
    """Log + span-tag an external API failure with the actual (redacted,
    truncated) response payload. Call this alongside — not instead of — the
    existing emit_external_api(...) metric call at every failure branch.

    `body` is the raw response text (or the text that failed to parse, for
    post-parse failures like malformed JSON). `error` is an exception string
    for SDK-mediated failures (Azure OpenAI / Azure AI Search) where there's
    no raw HTTP response to read.
    """
    text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else (body or "")
    safe_body = _redact(text)[:_MAX_BODY_CHARS]
    safe_url = _redact(url) if url else None

    log.warning(
        "external API failure: source=%s tool=%s status=%s url=%s error=%s body=%s",
        source, tool_name, status_code, safe_url, error, safe_body,
    )

    tag_span("error.source", source)
    tag_span("error.tool", tool_name)
    if status_code is not None:
        tag_span("error.status_code", status_code)
    if safe_url:
        tag_span("error.url", safe_url)
    if error:
        tag_span("error.message", error[:500])
    if safe_body:
        tag_span("error.response_body", safe_body)
