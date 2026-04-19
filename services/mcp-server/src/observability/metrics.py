import ddtrace.auto  # must be first import

import os
import logging

from ddtrace.internal.dogstatsd import get_dogstatsd_client

logger = logging.getLogger(__name__)

# Initialise DogStatsd client once at module import time.
# Falls back gracefully if DD_AGENT_HOST is not set (e.g. unit-test environments).
_dd_host = os.environ.get("DD_AGENT_HOST", "localhost")
_dd_port = int(os.environ.get("DD_DOGSTATSD_PORT", "8125"))

try:
    statsd = get_dogstatsd_client(f"udp://{_dd_host}:{_dd_port}")
except Exception as exc:  # pragma: no cover
    logger.warning("Failed to initialise DogStatsd client: %s", exc)
    statsd = None


def emit_tool_call(
    tool_name: str,
    latency_ms: float,
    status: str,
    result_count: int = 0,
) -> None:
    """Emit mcp.tool.* custom metrics for every tool invocation.

    Metrics emitted:
        mcp.tool.calls        (count)  tags: tool:<name>, status:success|error
        mcp.tool.latency_ms   (gauge)  tags: tool:<name>, status:<status>
        mcp.result.count      (gauge)  tags: tool:<name>   (only when result_count > 0)
    """
    if statsd is None:
        return

    tags = [
        f"tool:{tool_name}",
        f"status:{status}",
        "service:infratools-mcp",
    ]
    try:
        statsd.increment("mcp.tool.calls", tags=tags)
        statsd.gauge("mcp.tool.latency_ms", latency_ms, tags=tags)
        if result_count > 0:
            statsd.gauge("mcp.result.count", result_count, tags=tags)
    except Exception as exc:  # pragma: no cover
        logger.warning("emit_tool_call error: %s", exc)


def emit_external_api(
    source: str,
    latency_ms: float,
    error_type: str | None = None,
) -> None:
    """Emit mcp.external_api.* custom metrics for every outbound API call.

    Metrics emitted:
        mcp.external_api.latency_ms  (gauge)  tags: source:<name>
        mcp.external_api.errors      (count)  tags: source:<name>, error_type:<type>
                                              (only emitted when error_type is provided)
    """
    if statsd is None:
        return

    tags = [f"source:{source}"]
    try:
        statsd.gauge("mcp.external_api.latency_ms", latency_ms, tags=tags)
        if error_type:
            error_tags = tags + [f"error_type:{error_type}"]
            statsd.increment("mcp.external_api.errors", tags=error_tags)
    except Exception as exc:  # pragma: no cover
        logger.warning("emit_external_api error: %s", exc)
