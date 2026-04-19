import ddtrace.auto  # must be first import

import os
import time
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel

from observability.metrics import emit_tool_call

logger = logging.getLogger(__name__)

# Templates directory — relative to this file's package root
_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"

_TEMPLATE_MAP: dict[str, str] = {
    "scope_of_work": "scope_of_work.md.j2",
    "risk_summary": "risk_summary.md.j2",
    "cost_estimate_scaffold": "cost_estimate_scaffold.md.j2",
    "funding_positioning_memo": "funding_positioning_memo.md.j2",
}

# Jinja2 environment — load templates from the templates directory
_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape([]),  # Markdown output — no HTML escaping
    trim_blocks=True,
    lstrip_blocks=True,
)


class DraftDocumentInput(BaseModel):
    document_type: Literal[
        "scope_of_work",
        "risk_summary",
        "cost_estimate_scaffold",
        "funding_positioning_memo",
    ]
    context: dict[str, Any]
    project_name: str | None = None
    client_name: str | None = None
    notes: str | None = None


async def draft_document(input: DraftDocumentInput) -> str | dict[str, Any]:
    """Generate a structured document scaffold using a Jinja2 template.

    This tool does NOT call an LLM. It applies a pre-defined Jinja2 template
    populated with the context dict from previous tool calls.

    Returns:
        Markdown string with populated section headers and asset data.
        On error, returns a structured error dict.
    """
    start = time.monotonic()
    status = "success"

    try:
        template_file = _TEMPLATE_MAP.get(input.document_type)
        if template_file is None:
            return {
                "error": f"Unknown document_type: {input.document_type!r}",
                "source": "draft_document",
                "retriable": False,
            }

        template = _jinja_env.get_template(template_file)

        rendered = template.render(
            project_name=input.project_name,
            client_name=input.client_name,
            notes=input.notes,
            context=input.context,
            generated_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        )
        return rendered

    except Exception as exc:
        status = "error"
        logger.exception("draft_document failed for type %r", input.document_type)
        return {
            "error": str(exc),
            "source": "draft_document",
            "retriable": False,
        }
    finally:
        latency_ms = (time.monotonic() - start) * 1000
        emit_tool_call("draft_document", latency_ms, status)
