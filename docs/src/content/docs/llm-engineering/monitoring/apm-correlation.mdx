---
title: APM correlation (logs + DBM)
description: Get every log line and SQL query onto the same trace as the LLM span that triggered it. Pure-OTel in .NET, ddtrace-native in Python.
sidebar:
  order: 2
---

import { Tabs, TabItem, Aside } from '@astrojs/starlight/components';

An LLMObs trace is only half the picture. The other half is the supporting cast: the log lines emitted while the trace was active, the SQL queries the tools ran, the HTTP calls to upstream APIs. This page is about wiring those signals to the same `trace_id` so one click in DD pivots between them.

## Log ↔ trace correlation

The contract DD's log pipeline expects is two top-level fields on each log line: `dd.trace_id` and `dd.span_id`, in decimal. Once they're there, the DD agent's log pipeline auto-correlates and the trace's "Logs" tab fills in.

<Tabs syncKey="lang">
  <TabItem label="Python (ddtrace)">

`ddtrace` injects log context automatically when `DD_LOGS_INJECTION=true` and you use the standard `logging` module:

```yaml
# helm/values.yaml (excerpt)
agentApi:
  env:
    DD_LOGS_INJECTION: "true"
    DD_LOG_FORMAT: "json"        # one JSON object per line for the DD csharp/python log parser
```

```python
# Anywhere in the codebase:
import logging
log = logging.getLogger(__name__)

log.info("query completed", extra={"query.domain": domain})
```

The injected `dd.trace_id` and `dd.span_id` ride along with every log call. Click any APM trace → Logs tab to see them.

  </TabItem>
  <TabItem label=".NET (OpenTelemetry)">

Pure OTel doesn't enrich Serilog out of the box. We need a small custom enricher that reads `Activity.Current` and writes the two fields:

```csharp
// services/agent-api-dotnet/Observability/DatadogTraceContextEnricher.cs
internal sealed class DatadogTraceContextEnricher : ILogEventEnricher
{
    public void Enrich(LogEvent logEvent, ILogEventPropertyFactory _)
    {
        var activity = Activity.Current;
        if (activity is null) return;

        var traceIdHex = activity.TraceId.ToHexString();   // 32 chars
        var spanIdHex  = activity.SpanId.ToHexString();    // 16 chars

        if (ulong.TryParse(traceIdHex.AsSpan(16), NumberStyles.HexNumber,
                CultureInfo.InvariantCulture, out var traceLow64) &&
            ulong.TryParse(spanIdHex, NumberStyles.HexNumber,
                CultureInfo.InvariantCulture, out var spanId))
        {
            logEvent.AddPropertyIfAbsent(new LogEventProperty(
                "dd.trace_id",
                new ScalarValue(traceLow64.ToString(CultureInfo.InvariantCulture))));
            logEvent.AddPropertyIfAbsent(new LogEventProperty(
                "dd.span_id",
                new ScalarValue(spanId.ToString(CultureInfo.InvariantCulture))));
        }
    }
}
```

Wire it into `TelemetrySetup.cs`:

```csharp
builder.Host.UseSerilog((ctx, services, lc) => lc
    .Enrich.FromLogContext()
    .Enrich.With(new DatadogTraceContextEnricher())
    .Enrich.WithProperty("service.name", "infra-advisor-agent-api-dotnet")
    .WriteTo.Console(new RenderedCompactJsonFormatter()));
```

```yaml
# deployment.yaml annotation
ad.datadoghq.com/agent-api-dotnet.logs: '[{"source":"csharp","service":"infra-advisor-agent-api-dotnet"}]'
```

**Why lower 64 bits in decimal?** DD's log pipeline matches W3C 128-bit trace IDs by their low 64 bits in decimal — the same shape the DD .NET tracer historically emitted. The high 64 bits are stored separately and recombined at correlation time.

  </TabItem>
</Tabs>

## SQL ↔ trace correlation (DBM)

Database Monitoring links slow query samples to the trace that issued them. The classic DD .NET / Java tracer accomplishes this via SQL-comment injection (`DD_DBM_PROPAGATION_MODE=full`). With pure OTel, we use **attribute-based correlation** — same outcome, no SQL rewriting.

<Tabs syncKey="lang">
  <TabItem label="Python (ddtrace)">

ddtrace handles DBM via SQL-comment injection when configured. Set the propagation mode in the pod env:

```yaml
agentApi:
  env:
    DD_DBM_PROPAGATION_MODE: "full"
    DD_TRACE_SQLALCHEMY_ENABLED: "true"
    DD_TRACE_ASYNCPG_ENABLED: "true"
```

The DB integrations (asyncpg / SQLAlchemy) inject `dddbs`, `traceparent`, and `dddb` comments into every statement. DD's Postgres integration on the database side parses the comment and joins to the trace.

  </TabItem>
  <TabItem label=".NET (OpenTelemetry)">

Three pieces, all attribute-based:

**1. Npgsql.OpenTelemetry on the app side** — auto-emits the required attributes on every command span: `db.system`, `db.statement`, `db.name`, `db.user`, `peer.hostname`. App code just calls `.AddNpgsql()`:

```csharp
.WithTracing(t => t
    .AddAspNetCoreInstrumentation()
    .AddHttpClientInstrumentation()
    .AddNpgsql()                       // ← this
    ...)
```

**2. DD agent OTel collector processor** tags Npgsql spans with `span.type=sql`. DD's DBM ingest correlates spans-to-samples by statement match + timing window when this tag is present:

```yaml
# datadog/datadog-agent.yaml (otelCollector config)
processors:
  attributes/dbm:
    include:
      match_type: regexp
      attributes:
        - key: db.system
          value: ".+"
    actions:
      - key: span.type
        value: sql
        action: insert
```

**3. Postgres pod** runs the standard DD postgres integration via autodiscovery annotation, with `dbm:true` and `reported_hostname`:

```yaml
ad.datadoghq.com/postgres.checks: |
  {"postgres":{"instances":[{
    "dbm": true,
    "reported_hostname": "infra-advisor-postgres",
    "collect_schemas": {"enabled": true},
    "collect_activity_metrics": true,
    "relations": [{"relation_regex": ".*"}]
  }]}}
```

**Reference:** [DD OTel DBM correlation](https://docs.datadoghq.com/opentelemetry/correlate/dbm_and_traces/).

  </TabItem>
</Tabs>

## What you should see

After correlation is wired, opening any LLMObs trace shows:

1. **Logs** tab → the structured log lines emitted while the trace was active, in chronological order.
2. **Database** tab (if SQL was involved) → links to DBM's "Top queries" with the slow statement's full plan + waits.
3. **Metrics** tab → CPU/memory of the pod that handled the request, scoped to the trace window.
4. **RUM** tab → "View session replay" launches the user's actual click stream.

If any of these tabs are empty when they shouldn't be, the cause is almost always one of:
- Missing field name (`dd.trace_id`/`dd.span_id` not in log JSON).
- Wrong autodiscovery annotation (`source: csharp` vs `source: dotnet`).
- The "host" the DB reports differs from what APM expects (set `reported_hostname` explicitly).

<Aside type="tip">
**Smoke test:** fire a query, open the trace, click each tab. If any one is empty after the agent has had 1–2 minutes to ingest, the correlation for that signal isn't wired correctly. Don't trust silent empties.
</Aside>

## What's next

- [MCP clients](../mcp-clients/) — propagating the same `trace_id` across services.
- [Spans and traces](../spans-and-traces/) — the LLMObs queries that benefit from this correlation.
