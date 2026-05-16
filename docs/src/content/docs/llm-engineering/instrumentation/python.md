---
title: Python instrumentation with ddtrace
description: Auto-instrument LangChain, LangGraph, OpenAI, and the MCP client with ddtrace, then layer explicit LLMObs.workflow/agent/task spans for orchestration.
sidebar:
  order: 1
  label: Python (ddtrace)
---

import { Aside } from '@astrojs/starlight/components';

ddtrace is the right choice when your stack is LangChain / LangGraph / OpenAI / a popular orchestrator. It auto-patches all of them and gives you usable LLMObs traces with very little code. The explicit `LLMObs.workflow/agent/task` decorators are reserved for orchestration spans (routing, planning, post-processing) that have no auto-patchable equivalent.

**Source of truth in this repo:** `services/agent-api/src/`.

## What gets auto-instrumented

When `ddtrace.auto` is the first import and `DD_LLMOBS_ENABLED=true` is set, the following operations produce LLMObs spans without any code change:

| Operation | Integration | LLMObs span kind |
|---|---|---|
| `AzureChatOpenAI.ainvoke()` / `ChatOpenAI.ainvoke()` | LangChain | `chat_model` |
| `AsyncAzureOpenAI().chat.completions.create()` | OpenAI | `llm` |
| `BaseTool.ainvoke()` (incl. MCP-adapter tools) | LangChain | `tool` |
| `CompiledGraph.ainvoke()` (LangGraph) | LangGraph | per-node spans |
| `ClientSession.call_tool()` (MCP client) | MCP | `tool` |
| `BasePromptTemplate.ainvoke()` | LangChain | `chain` |

Token counts, model name, prompt + completion content, and cost (when LLMObs has model pricing in its catalog) come along for free.

## Bootstrap

`ddtrace.auto` MUST be the first import — it patches downstream libraries on import. Putting it after `import langchain_openai` will leave `langchain_openai` un-patched and your trace will have a hole where the LLM call should be.

```python
# services/agent-api/src/agent.py
import ddtrace.auto  # must be first import — auto-instruments LangChain, LangGraph, OpenAI, MCP, httpx, Redis, Kafka

import json
import logging
import os
from ddtrace.llmobs import LLMObs
from langchain_openai import AzureChatOpenAI
from langgraph.prebuilt import create_react_agent
# ... rest of imports
```

Then on FastAPI startup, call `enable_llm_obs()` (a tiny wrapper around `LLMObs.enable`):

```python
# services/agent-api/src/observability/llm_obs.py
def enable_llm_obs() -> None:
    ml_app = os.environ.get("DD_LLMOBS_ML_APP", "infra-advisor-ai")
    agentless = os.environ.get("DD_LLMOBS_AGENTLESS_ENABLED", "false").lower() == "true"
    try:
        LLMObs.enable(ml_app=ml_app, agentless_enabled=agentless)
        logger.info("LLMObs enabled ml_app=%s agentless=%s", ml_app, agentless)
    except Exception as exc:
        logger.warning("LLMObs.enable() failed (non-fatal): %s", exc)
```

The try/except is deliberate — a misconfigured LLMObs should never take the service down. Telemetry is supplementary, not load-bearing.

## Explicit orchestration spans

InfraAdvisor's agent pipeline has three phases that don't map to anything ddtrace auto-instruments: history load, routing, and source extraction. We wrap those with `LLMObs.workflow/agent/task` context managers so the trace shows the full decision tree, not just the LLM calls inside.

```python
# services/agent-api/src/agent.py (excerpt)
with LLMObs.workflow("query-processing") as workflow_span:
    LLMObs.annotate(
        span=workflow_span,
        input_data={"content": query, "role": "user"},
        tags={"session.id": session_id, "query.domain": "tbd"},
    )

    with LLMObs.task("load-history") as history_span:
        history = await load_history(session_id)
        LLMObs.annotate(span=history_span, tags={"history.turns": len(history)})

    with LLMObs.agent("router") as router_span:
        decision = await _route(query, history)        # auto-instrumented chat_model child
        LLMObs.annotate(span=router_span, tags={"query.domain": decision.specialist})

    with LLMObs.agent(f"specialist-{decision.specialist}") as agent_span:
        result = await react_agent.ainvoke(...)         # auto-instrumented LangGraph children
        tag_agent_run(agent_span, query, result["output"], decision.specialist, tools_called=...)

    with LLMObs.task("extract-sources") as sources_span:
        sources = _extract_sources(result)
        LLMObs.annotate(span=sources_span, tags={"sources.count": len(sources)})
```

Span-kind cheat sheet:
- `LLMObs.workflow()` — the top-level wrapper for one logical request.
- `LLMObs.agent()` — a decision-making unit (router, planner, specialist).
- `LLMObs.task()` — deterministic step (DB lookup, parsing, formatting).
- `LLMObs.tool()` — a tool invocation. We don't use this manually; ddtrace's MCP/LangChain integrations cover all of ours.
- `LLMObs.llm()` — a raw LLM call. We don't use this manually either; the integrations cover all of ours.

If you wrap an auto-instrumented call in `LLMObs.llm()` you get a duplicate span — the integration emits one, your wrapper emits another. Skip it.

## Annotating spans with rich metadata

`LLMObs.annotate(span, input_data, output_data, tags)` is how you tell LLMObs what a span actually did. Two patterns we use:

**At span open** — attach inputs you already have:

```python
LLMObs.annotate(
    span=workflow_span,
    input_data={"content": query, "role": "user"},
    tags={"session.id": session_id, "query.domain": "tbd"},
)
```

**Inside or before span close** — attach outputs and final tags. Must happen while the context manager is still open, because `annotate` writes to the *active* span:

```python
def tag_agent_run(span, query, answer, query_domain, tools_called, cost_usd=None):
    LLMObs.annotate(
        span=span,
        input_data={"content": query, "role": "user"},
        output_data={"content": answer, "role": "assistant"},
        tags={
            "query.domain": query_domain,
            "agent.tools_called": ",".join(tools_called),
            **({"llm.cost_usd": str(cost_usd)} if cost_usd is not None else {}),
        },
    )
```

The mistake to avoid: calling `LLMObs.annotate()` *after* the `with` block. By that point the span is closed and your tags go nowhere (silently — no error).

## Session linking with RUM

InfraAdvisor's UI sets `X-DD-RUM-Session-ID` on every `/query` call. We use that as the LLMObs `session.id` when present, so LLMObs sessions group by browser session, which links straight to RUM session replay:

```python
rum_session = request.headers.get("X-DD-RUM-Session-ID")
session_id = rum_session or internal_chat_uuid

LLMObs.annotate(
    span=workflow_span,
    tags={
        "session.id": session_id,
        "session.chat_id": internal_chat_uuid,
        **({"session.rum_id": rum_session} if rum_session else {}),
    },
)
```

In DD UI, click any LLMObs trace → "View session replay" to land on the user's actual click stream.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| No traces in DD | `ddtrace.auto` not first import | Move it to line 1 of the entrypoint; rebuild |
| Traces appear but no LLMObs classification | `DD_LLMOBS_ENABLED` not set or `enable_llm_obs()` not called | Check pod env + lifespan startup logs for "LLMObs enabled" |
| Spans show up but with no model / tokens | Wrong ddtrace version | Bump to ≥3.11; older versions miss LangChain v0.3 / MCP |
| `agentless_enabled=true` but nothing flows | Missing `DD_API_KEY` | Set the secret; agentless requires direct API auth |
| Wrapped a LangChain call in `LLMObs.llm()` and now see two spans | Duplicate wrapping | Remove the manual wrapper; ddtrace already emits the span |

## What's next

- [Monitoring → Spans and traces](../../monitoring/spans-and-traces/) — what the trace tree looks like in DD UI and how to query it.
- [Evaluations → External](../../evaluations/external/) — attaching faithfulness, relevance, user-feedback scores to these traces.
- [Monitoring → Metrics](../../monitoring/metrics/) — business counters that ride alongside the trace pipeline.
