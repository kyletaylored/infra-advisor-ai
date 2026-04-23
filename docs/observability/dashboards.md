---
title: Dashboards & Monitors
parent: Observability
nav_order: 4
---

# Dashboards & Monitors

All Datadog dashboards and monitors are defined as JSON in the `datadog/` directory and can be imported via the Datadog API or UI.

## Dashboards

### Infrastructure Overview (`datadog/dashboards/infra-overview.json`)

**Purpose:** Cluster-level health at a glance.

**Widgets:**
- Ready nodes / total nodes
- Pod count by namespace
- Container restart rate (rolling 1h)
- Kafka broker messages/sec (in and out)
- Kafka consumer lag by consumer group
- Redis operations/sec
- Redis memory utilization %
- Network I/O per node (ingress/egress bytes)

**Primary audience:** Platform/infra team, on-call engineer

---

### LLM Observability (`datadog/dashboards/llm-observability.json`)

**Purpose:** Agent quality and usage metrics.

**Widgets:**
- Query volume (requests/min over 24h)
- P50 / P95 / P99 query latency by specialist (engineering, water_energy, business_dev, document, general)
- Total prompt + completion tokens per hour
- Estimated cost per hour (by model)
- Faithfulness score distribution (histogram)
- User feedback breakdown (positive/negative/reported)
- Tool call distribution (which tools get called most)
- Session count (unique session IDs per hour)

**Primary audience:** Product/AI team, demo presenter

---

### MCP Server (`datadog/dashboards/mcp-server.json`)

**Purpose:** Tool-level metrics and external API health.

**Widgets:**
- Tool call count by tool name (timeseries)
- Tool success rate by tool name
- Tool latency p95 by tool name
- External API latency by source (ArcGIS NBI, OpenFEMA, EIA, EPA SDWIS, SAM.gov, Tavily)
- External API error rate by source
- Error count by tool + error type (toplist)

**Primary audience:** Engineering team debugging tool failures

---

### Pipeline Health (`datadog/dashboards/pipeline-health.json`)

**Purpose:** Airflow data ingestion pipeline status.

**Widgets:**
- DAG run duration by DAG name (timeseries)
- Task success rate by DAG
- Records fetched per DAG per run (from custom metric)
- Azure Blob Storage upload latency by DAG
- Upload size by DAG
- Azure AI Search document count by domain
- DJM: recent DAG runs table (via Datadog DJM widget)

**Primary audience:** Data engineering team, demo of DJM

---

### Blob Storage (`datadog/dashboards/blob-storage.json`)

**Purpose:** Azure Blob Storage upload tracking from Airflow DAGs.

**Widgets:**
- Upload throughput (span count/min) by `dag_id` tag
- Upload p95 latency by `dag_id`
- Error rate by `dag_id`
- Average upload size (bytes) by `dag_id`
- 24h total uploads (query value)
- 24h error count (query value)
- Top upload paths (toplist, last 1h)
- Uploads by container (timeseries)

**Data source:** APM spans with operation `azure.blob.upload` from `_dd_blob.py`

---

## Monitors

### Faithfulness Score Alert (`datadog/monitors/faithfulness-score.json`)

**Condition:** Mean `eval.faithfulness_score` < 0.75 over 1 hour  
**Priority:** P2  
**Notify:** On-call channel  
**Interpretation:** Agent answers are not well-grounded in retrieved sources. Common causes: Azure AI Search index is empty (run `knowledge_base_init`), model prompt drift, or retrieval quality degradation.

---

### Kafka Consumer Lag (`datadog/monitors/kafka-consumer-lag.json`)

**Condition:** Consumer lag on `infra.query.events` > 10,000 messages  
**Priority:** P2  
**Notify:** On-call channel  
**Interpretation:** The Agent API Kafka consumer has fallen behind the load generator. May indicate: Agent API pod crash, Redis connection failure, or MCP Server unresponsive.

---

### MCP External API Error Rate (`datadog/monitors/mcp-external-api-error.json`)

**Condition:** Error rate on `mcp.external_api.errors` > 5% over 5 minutes  
**Priority:** P3  
**Notify:** Engineering channel  
**Interpretation:** One or more external government APIs (ArcGIS, FEMA, EIA, etc.) are returning errors. Check the MCP Server dashboard for which source is failing.

---

## Synthetics

### Consultant Query Flow (`datadog/synthetics/consultant-query-flow.json`)

**Type:** Browser test  
**Frequency:** Every 5 minutes  
**Location:** AWS us-east-1 (Datadog managed)

**Steps:**
1. Navigate to `https://infra-advisor-ai.kyletaylor.dev`
2. Log in with test credentials
3. Submit query: "Show structurally deficient bridges in Harris County TX"
4. Assert: Response appears within 30 seconds
5. Assert: Citation panel shows at least one source
6. Click thumbs up feedback button
7. Assert: No JavaScript errors

**Alerts:** Notifies on-call if synthetic test fails for 2 consecutive runs.

---

## Importing dashboards and monitors

```bash
# Import a dashboard via Datadog API
curl -X POST "https://api.us3.datadoghq.com/api/v1/dashboard" \
  -H "DD-API-KEY: ${DD_API_KEY}" \
  -H "DD-APPLICATION-KEY: ${DD_APP_KEY}" \
  -H "Content-Type: application/json" \
  -d @datadog/dashboards/llm-observability.json

# Import a monitor
curl -X POST "https://api.us3.datadoghq.com/api/v1/monitor" \
  -H "DD-API-KEY: ${DD_API_KEY}" \
  -H "DD-APPLICATION-KEY: ${DD_APP_KEY}" \
  -H "Content-Type: application/json" \
  -d @datadog/monitors/faithfulness-score.json
```
