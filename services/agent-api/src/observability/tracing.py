"""Thin tracing helpers for agent-api."""

from ddtrace import tracer


def current_trace_id() -> str | None:
    """Return the current Datadog trace ID as a hex string, or None."""
    span = tracer.current_span()
    if span is None:
        return None
    return format(span.trace_id, "x")


def current_span_id() -> str | None:
    """Return the current Datadog span ID as a hex string, or None."""
    span = tracer.current_span()
    if span is None:
        return None
    return format(span.span_id, "x")


def tag_span(key: str, value: object) -> None:
    """Tag the current active span. No-op if there is no active span."""
    span = tracer.current_span()
    if span is not None:
        span.set_tag(key, str(value))
