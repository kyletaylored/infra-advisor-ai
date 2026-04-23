"""
Airflow logging config with Datadog trace-log correlation (JSON output).

Set in Airflow env:
  AIRFLOW__LOGGING__LOGGING_CONFIG_CLASS=_dd_logging.LOGGING_CONFIG

The module-level ddtrace import runs when Airflow initialises logging in every
process (scheduler main, LocalExecutor task subprocesses, dag-processor).  This
ensures trace IDs are available in task log records without touching each DAG file.
ddtrace is not installed in the dag-processor, so the import is guarded.
"""

import json
import logging
import os
from copy import deepcopy

try:
    import ddtrace.auto  # noqa: F401 — patches logging with dd.trace_id/span_id
except ImportError:
    pass

try:
    from airflow.config_templates.airflow_local_settings import DEFAULT_LOGGING_CONFIG as _BASE
    _BASE_LOADED = True
except Exception:
    _BASE_LOADED = False

# ---------------------------------------------------------------------------
# JSON formatter
# ---------------------------------------------------------------------------

class DDJsonFormatter(logging.Formatter):
    """Emit one JSON object per log record, including Datadog trace context."""

    def format(self, record: logging.LogRecord) -> str:
        # Build message first so exc_info rendering (via super) runs once
        record.message = record.getMessage()
        if self.usesTime():
            record.asctime = self.formatTime(record, self.datefmt)

        entry: dict = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.message,
            # dd.* injected by ddtrace when DD_LOGS_INJECTION=true; default to 0 otherwise
            "dd.trace_id": str(getattr(record, "dd.trace_id", "0") or "0"),
            "dd.span_id": str(getattr(record, "dd.span_id", "0") or "0"),
            "dd.env": os.environ.get("DD_ENV", ""),
            "dd.service": os.environ.get("DD_SERVICE", "airflow-scheduler"),
            "dd.version": os.environ.get("DD_VERSION", ""),
        }

        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            entry["exception"] = record.exc_text
        if record.stack_info:
            entry["stack_info"] = self.formatStack(record.stack_info)

        # Include dag/task context if present (added by Airflow's TaskInstanceLogKey)
        for _ctx in ("dag_id", "task_id", "run_id", "try_number", "map_index"):
            val = record.__dict__.get(_ctx)
            if val is not None:
                entry[_ctx] = val

        return json.dumps(entry, default=str)


# ---------------------------------------------------------------------------
# Build logging config
# ---------------------------------------------------------------------------

def _build_config() -> dict:
    if not _BASE_LOADED:
        return {}

    config = deepcopy(_BASE)

    # Register our formatter
    config.setdefault("formatters", {})["dd_json"] = {
        "()": "_dd_logging.DDJsonFormatter",
    }

    # Apply to handlers that produce visible output (keep SecretsMasker filters)
    for handler_name in ("task", "console", "processor_to_stdout"):
        if handler_name in config.get("handlers", {}):
            config["handlers"][handler_name]["formatter"] = "dd_json"

    return config


LOGGING_CONFIG = _build_config()
