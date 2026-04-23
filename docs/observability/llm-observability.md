---
title: LLM Observability
parent: Observability
nav_order: 2
---

# LLM Observability

Every query to the Agent API produces a complete LLM Observability trace in Datadog. The span tree captures the full multi-agent pipeline from initial routing through final answer synthesis, with automatic attribution of token usage, cost, and latency to each sub-operation.

Navigate to **Datadog → LLM Observability** to explore traces, session views, and evaluation scores.

## Span tree structure

```
workflow: query-processing                           ← root span (entire query lifecycle)
  │  Tags: query.domain, session.id, session.chat_id
  │
  ├── task: load-history                             ← Redis history lookup
  │     Tags: history.turns (count of prior exchanges)
  │
  ├── agent: router                                  ← domain classification
  │     Tags: query.domain (engineering|water_energy|business_dev|document|general)
  │     └── chat_model (auto-instrumented)           ← gpt-4.1-mini LLM call
  │           Tokens: ~200 prompt / ~50 completion
  │
  ├── agent: planner                                 ← execution strategy
  │     └── chat_model (auto-instrumented)           ← gpt-4.1-mini LLM call
  │           Tokens: ~300 prompt / ~80 completion
  │
  ├── agent: infra-advisor                           ← LangGraph ReAct executor
  │     Tags: tools_called.count, sources.count
  │     ├── tool: get_bridge_condition               ← MCP tool call (auto-instrumented)
  │     │     └── http (outbound to mcp-server)      ← auto-instrumented
  │     └── chat_model (auto-instrumented)           ← ReAct reasoning LLM calls
  │           (1–N iterations until answer complete)
  │
  └── task: extract-sources                          ← post-processing
        Tags: sources.count

(async, separate trace)
task: faithfulness-eval
  └── llm (auto-instrumented)                        ← gpt-4.1-nano eval call
        Tags: eval.faithfulness_score, eval.model
```

## Auto-instrumented vs explicit spans

ddtrace auto-instruments several LangChain/LangGraph operations when LLM Obs is enabled:

| Operation | Instrumented by | Span type |
|-----------|----------------|-----------|
| `ChatOpenAI.ainvoke()` / `AzureChatOpenAI.ainvoke()` | ddtrace LangChain integration | `chat_model` |
| `BasePromptTemplate.ainvoke()` | ddtrace LangChain integration | `chain` |
| `BaseTool.ainvoke()` (MCP tools) | ddtrace LangChain integration | `tool` |
| `CompiledGraph.ainvoke()` (LangGraph) | ddtrace LangGraph integration | nested spans per node |
| `AsyncAzureOpenAI().chat.completions.create()` | ddtrace OpenAI integration | `llm` |

Explicit `LLMObs.workflow()`, `LLMObs.agent()`, and `LLMObs.task()` decorators are used for the orchestration spans that have no auto-instrumented equivalent.

No `LLMObs.llm()` wrappers are used — all LLM calls are auto-instrumented, avoiding duplicate span nesting.

## Session grouping

All spans for a query are tagged with `session.id`. When the browser sends `X-DD-RUM-Session-ID`, this is used as the `session.id` value (enabling RUM session → LLM Obs linking). Otherwise, the internal Redis session UUID is used.

In **LLM Obs → Sessions**, all queries from the same browser session appear grouped, with timeline ordering and total token/cost aggregation.

## User feedback evaluations

When a user clicks thumbs up/down/flag, `POST /api/feedback` is called:

```python
LLMObs.submit_evaluation(
    span_context={"trace_id": trace_id, "span_id": span_id},
    label="user_feedback",
    metric_type="categorical",
    value="positive"  # or "negative" / "reported"
)
```

The evaluation appears under the **Evaluations** tab on the specific LLM Obs span. You can build monitors and dashboards on `@llm_obs.evaluations.user_feedback`.

## Faithfulness evaluation

An async fire-and-forget task runs after every query (both real user queries and synthetic load generator queries) to score answer faithfulness:

**Prompt:**
```
Given the following query and answer, rate how well the answer is grounded in the 
retrieved source documents (0.0 = hallucinated, 1.0 = fully grounded):

Query: {query}
Answer: {answer}
Sources: {source_snippets}

Return a JSON object: {"score": float, "reasoning": str}
```

The score is submitted as a `faithfulness` evaluation on the workflow span.

**Monitor:** `datadog/monitors/faithfulness-score.json` alerts when the mean faithfulness score drops below 0.75 over a 1-hour window.

## ML App configuration

```
DD_LLMOBS_ENABLED=true
DD_LLMOBS_ML_APP=infra-advisor-ai
DD_LLMOBS_AGENTLESS_ENABLED=false   ← uses Datadog Agent on-cluster for export
```

## Span tags reference

| Tag | Where set | Values |
|-----|-----------|--------|
| `query.domain` | router agent | engineering, water_energy, business_development, document, general |
| `session.id` | workflow root | RUM session ID or Redis chat UUID |
| `session.chat_id` | workflow root | Redis session key UUID |
| `session.rum_id` | workflow root (when RUM header present) | Browser RUM session ID |
| `history.turns` | load-history task | Integer (0 for first query) |
| `tools_called.count` | specialist agent | Integer |
| `sources.count` | extract-sources task | Integer |
| `eval.faithfulness_score` | faithfulness-eval task | Float 0.0–1.0 |
| `eval.model` | faithfulness-eval task | `gpt-4.1-nano` |
