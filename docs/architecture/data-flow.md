---
title: Data Flow
parent: Architecture
nav_order: 2
---

# Data Flow

## Query request lifecycle

A consultant types a question. Here is the complete path from browser to response, including every observability event produced along the way.

```
1. BROWSER
   User types: "List bridges in Harris County with sufficiency < 50"
   
   Datadog RUM: custom event query_submitted {query, session_id}
   X-DD-RUM-Session-ID header injected by RUM SDK

2. NGINX (UI pod)
   POST /api/query → proxied to agent-api:8001/query

3. AGENT API — main.py
   Validates JWT token (checks Authorization: Bearer header)
   Reads session history from Redis (24-hour TTL key)
   Resolves model: request body → session Redis key → default (gpt-4.1-mini)
   Opens LLMObs.workflow("query-processing") span
   
   Datadog LLM Obs: workflow root span opened
   Datadog APM: HTTP span created (auto ddtrace)

4. AGENT API — agent.py — Router
   Opens LLMObs.agent("router")
   Calls gpt-4.1-mini with _ROUTER_PROMPT to classify domain
   Returns: {specialist: "engineering", handoff_context: "bridge query, TX county"}
   
   Datadog LLM Obs: router agent span + nested chat_model span
   Tokens: ~200 prompt / ~50 completion

5. AGENT API — agent.py — Planner
   Opens LLMObs.agent("planner")
   Calls gpt-4.1-mini: produces 1–2 sentence execution strategy
   Strategy injected as SystemMessage hint before executor runs
   
   Datadog LLM Obs: planner agent span + nested chat_model span

6. AGENT API — agent.py — Specialist Executor (LangGraph ReAct)
   Opens LLMObs.agent("infra-advisor")
   Executor receives engineering tool subset: [get_bridge_condition, get_disaster_history,
     get_energy_infrastructure, get_water_infrastructure, get_ercot_energy_storage,
     search_txdot_open_data, search_project_knowledge, draft_document]
   
   ReAct loop iteration 1:
   a. LLM decides: call get_bridge_condition(state="TX", county="Harris", max_sufficiency=50)
   b. MCP tool call → HTTP POST to mcp-server:8000/mcp
   
   Datadog LLM Obs: tool call span under specialist
   Datadog APM: outbound HTTP span to mcp-server

7. MCP SERVER — bridge_condition.py
   Constructs ArcGIS FeatureServer query:
     WHERE: STATE_CODE_001='48' AND COUNTY_CODE_003='101' AND SUFFICIENCY_RATING<50
   Paginates (2,000 records/page) until < 2,000 returned
   Emits Datadog metric: mcp.tool.calls{tool:get_bridge_condition, status:success}
   Emits Datadog metric: mcp.external_api.latency_ms{source:arcgis_nbi}
   Returns: list of bridge dicts with structure number, condition ratings, etc.
   
   Datadog APM: outbound HTTP span to ArcGIS REST

8. AGENT API — ReAct loop iteration 2
   LLM sees bridge results, decides no further tool calls needed
   LLM generates final answer with citations
   
   Datadog LLM Obs: specialist agent span closed with final answer

9. AGENT API — extract-sources task
   Opens LLMObs.task("extract-sources")
   Parses tool call metadata from agent message history
   Extracts: tool names, sources, snippets for CitationPanel

10. AGENT API — response assembly
    Writes updated history to Redis
    Publishes eval event to Kafka infra.eval.results topic
    Returns JSON: {answer, sources, trace_id, span_id, session_id, model}
    
    Datadog LLM Obs: workflow span closed
    Datadog DSM: Kafka produce span

11. BROWSER — response rendering
    Chat.tsx renders markdown answer
    CitationPanel shows source cards (expandable, with external links)
    SuggestionCard shows 4 follow-up questions (from Redis pool)
    ExternalLink button renders with APM trace URL (trace_id from response)
    
    Datadog RUM: session replay frame recorded

12. ASYNC — faithfulness evaluation (fire-and-forget)
    Agent API background task evaluates answer grounding
    Opens LLMObs.task("faithfulness-eval")
    Calls Azure OpenAI gpt-4.1-nano: score 0–1 based on source fidelity
    Submits LLMObs.submit_evaluation("faithfulness", score)
    
    Datadog LLM Obs: faithfulness score appears under Evaluations tab on trace
```

## Data ingestion pipeline

Raw government data flows from external APIs through transformation into a searchable vector index.

```
GOVERNMENT APIs → AIRFLOW DAGS → AZURE BLOB STORAGE → AZURE AI SEARCH

Schedule:
  Daily 02:00 UTC    FEMA declarations (OpenFEMA paginated REST)
  Weekly Sun 03:00   FHWA NBI bridge records (ArcGIS FeatureServer, TX only)
  Weekly 04:00       EIA electricity generation/capacity (EIA API v2)
  Monthly 1st 05:00  TWDB water projects + EPA SDWIS water systems
  On-demand          Knowledge base initialization (LLM-generated synthetic docs)

Per-DAG task pattern (3 steps):

  Task 1: fetch_*_data()
    Paginate external API
    XCom push: list of record dicts

  Task 2: store_raw_parquet()
    XCom pull records
    Convert to Pandas DataFrame
    Serialize to Parquet (BytesIO)
    Upload to Azure Blob: raw-data/<domain>/filename_YYYYMMDD.parquet
    dd_upload_blob() emits: azure.blob.upload APM span

  Task 3: index_to_search()
    XCom pull records
    For each record:
      1. Generate narrative text (template or LLM)
      2. Chunk (character window or tiktoken 512-token/64-overlap)
      3. Embed: Azure OpenAI text-embedding-3-small → 1536-dim vector
      4. Build AI Search document:
           { id, content, content_vector, source, domain,
             document_type, state, county, <domain fields> }
    Upsert in 100-doc batches to Azure AI Search

DATADOG INTEGRATION IN PIPELINE:
  OpenLineage → Datadog DJM: DAG run duration, task status, lineage graph
  ddtrace.auto: APM spans on all HTTP + blob upload + search upsert calls
  DDJsonFormatter: JSON task logs with dd.trace_id/dd.span_id (log-trace correlation)
  sitecustomize.py: initializes ddtrace in every LocalExecutor task subprocess
```

## Eval loop

Synthetic load validates agent quality continuously, without requiring real user traffic.

```
Load Generator CronJob (every 5 min)
  Samples 10–20 queries from YAML corpora (70% happy path, 20% edge, 10% adversarial)
  Publishes each to Kafka topic: infra.query.events
  {query_id, session_id, query, corpus_type, domain, timestamp_ms}
  
  Datadog DSM: producer span on infra.query.events

Agent API Kafka Consumer (background thread)
  Reads from infra.query.events
  Runs run_agent(query, session_id) for each message
  Faithfulness score computed asynchronously
  Publishes eval result to infra.eval.results
  {query_id, session_id, answer, faithfulness_score, latency_ms, model}
  
  Datadog DSM: consumer span (consumer lag monitored)
  Datadog LLM Obs: full trace tree per synthetic query
  Datadog monitor: alert if faithfulness mean < 0.75
```

## Session memory

Redis stores two types of data for the Agent API:

```
Key pattern                                      TTL       Content
─────────────────────────────────────────────────────────────────────
infra-advisor:session:{uuid}:memory             86400s    JSON list of HumanMessage/AIMessage exchanges
infra-advisor:session:{uuid}:model              86400s    String: Azure OpenAI deployment name
infra-advisor:suggestions:pool                  none      Redis List: up to 80 AECOM-focused suggestions
```

The suggestion pool is maintained by a background asyncio task (`_pool_maintenance_loop`) that:
- Seeds the pool on startup if empty
- Refills to 80 items every 30 minutes when pool drops below 20
- Uses 4 rotating batch prompts (Engineering / Construction / Operations / Management focus)

Page loads draw 4 random items from the pool in ~1ms with no LLM wait.
