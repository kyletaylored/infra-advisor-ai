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

Pure OTel doesn't enrich Serilog out of the box — the DD .NET tracer used to inject `dd.trace_id` / `dd.span_id` automatically when `DD_LOGS_INJECTION=true`, but we no longer run that tracer. A small custom Serilog enricher (`DatadogTraceContextEnricher`) reads `Activity.Current` and writes the two fields onto every log event.

```csharp
// DatadogTraceContextEnricher.cs
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

The DD agent's container log collector recognizes the `csharp` source and parses these two fields verbatim:

```yaml
# deployment.yaml annotation
ad.datadoghq.com/agent-api-dotnet.logs: '[{"source":"csharp","service":"infra-advisor-agent-api-dotnet"}]'
```

Why lower 64 bits in decimal? DD's log-trace correlation pipeline matches W3C 128-bit trace IDs by their low 64 bits in decimal form — the same shape the DD .NET tracer historically emitted. The agent stores the high 64 bits separately and recombines them at correlation time. Click any APM trace → Logs tab at the bottom shows the matching structured log lines.

## References

- [DD log-trace correlation (OTel)](https://docs.datadoghq.com/tracing/other_telemetry/connect_logs_and_traces/opentelemetry/)
- [DD OTel DBM correlation](https://docs.datadoghq.com/opentelemetry/correlate/dbm_and_traces/)
