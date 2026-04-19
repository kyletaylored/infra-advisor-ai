import ddtrace.auto  # must be first import

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
