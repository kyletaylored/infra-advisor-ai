---
title: DBM + log correlation
description: Attribute-based DBM↔APM correlation without SQL-comment injection, and Serilog-based log↔trace linkage.
sidebar:
  order: 5
  label: Correlation
---

## DBM ↔ APM correlation (attribute-based, no SQL-comment injection)

**Goal:** when a query span is slow in the APM trace tree, jump straight to the matching pg_stat_activity sample in Database Monitoring.

The Datadog .NET tracer accomplishes this via SQL-comment injection (`DD_DBM_PROPAGATION_MODE=full`). With OTel only, we use **attribute-based correlation** instead — same outcome, no SQL rewriting.

**How we wired it:**

1. **Npgsql.OpenTelemetry** auto-emits the required attributes on every command span: `db.system`, `db.statement`, `db.name`, `db.user`, `peer.hostname`. App code only calls `.AddNpgsql()` in `TelemetrySetup.cs`.

2. **DD agent OTel collector processor** tags Npgsql spans with `span.type=sql`. DD's DBM ingest correlates spans-to-samples by statement match + timing window when this tag is present.

   ```yaml
   # datadog/datadog-agent.yaml (otelCollector config)
   processors:
     attributes/dbm:
       include:
         match_type: regexp
         attributes:
           - key: db.system
             value: ".+"          # matches "postgres" and "postgresql"
       actions:
         - key: span.type
           value: sql
           action: insert
   ```

3. **The Postgres pod** runs the standard DD postgres integration via autodiscovery annotation, with `dbm:true` and `reported_hostname` set so the host renders as a stable name in the DBM UI:

   ```yaml
   ad.datadoghq.com/postgres.checks: |
     {"postgres":{"instances":[{
       "dbm": true,
       "reported_hostname": "infra-advisor-postgres",
       "collect_schemas":         {"enabled": true},
       "collect_settings":        {"enabled": true},
       "collect_column_statistics": {"enabled": true},
       "collect_function_metrics":   true,
       "collect_activity_metrics":   true,
       "collect_wal_metrics":        true,
       "collect_bloat_metrics":      true,
       "collect_buffercache_metrics":true,
       "relations": [{"relation_regex": ".*"}]
     }]}}
   ```

**Reference:** [Datadog OTel DBM correlation](https://docs.datadoghq.com/opentelemetry/correlate/dbm_and_traces/).

---

## Log → trace correlation

OTel SDK + Serilog gives you log ↔ trace linkage automatically when:

1. Serilog enriches logs from the `LogContext` (the request's ambient Activity).
2. Logs emit in JSON with the OTel trace/span ID fields (`@tr`, `@sp`).
3. The DD agent's container log collector recognizes the `csharp` source and parses those fields.

```csharp
// TelemetrySetup.cs
builder.Host.UseSerilog((ctx, services, lc) => lc
    .Enrich.FromLogContext()
    .Enrich.WithProperty("service.name", "infra-advisor-agent-api-dotnet")
    .WriteTo.Console(new RenderedCompactJsonFormatter()));
```

```yaml
# deployment.yaml annotation
ad.datadoghq.com/agent-api-dotnet.logs: '[{"source":"csharp","service":"infra-advisor-agent-api-dotnet"}]'
```

That's it. Click any APM trace → Logs tab at the bottom shows the matching structured log lines. No `DD_LOGS_INJECTION`, no enricher, no DD .NET tracer.

## References

- [DD log-trace correlation (OTel)](https://docs.datadoghq.com/tracing/other_telemetry/connect_logs_and_traces/opentelemetry/)
- [DD OTel DBM correlation](https://docs.datadoghq.com/opentelemetry/correlate/dbm_and_traces/)
