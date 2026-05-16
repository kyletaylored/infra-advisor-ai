using System.Diagnostics;
using System.Globalization;
using Serilog.Core;
using Serilog.Events;

namespace InfraAdvisor.AgentApi.Observability;

// Copies the ambient OTel Activity's trace/span IDs onto each Serilog log
// event so DD's log-trace correlation works for `source: csharp` logs.
//
// We used to rely on the DD .NET tracer's DD_LOGS_INJECTION=true to do
// this automatically. After moving to pure OTel (see troubleshooting:
// "Trace tree split across two trace IDs (dual-tracer)"), nothing was
// writing trace context to logs — APM trace → Logs tab came up empty.
//
// Field names match what DD's csharp log integration historically
// expected from the DD .NET tracer:
//   dd.trace_id  — lower 64 bits of the W3C trace_id, base-10
//   dd.span_id   — span_id, base-10
//
// Serilog's LogEventProperty allows literal "." in property names; the
// CompactJsonFormatter emits the name as a JSON key, which is what DD
// wants. Dots aren't valid in {Template} placeholders, but we never
// reference these via a template.
internal sealed class DatadogTraceContextEnricher : ILogEventEnricher
{
    public void Enrich(LogEvent logEvent, ILogEventPropertyFactory propertyFactory)
    {
        var activity = Activity.Current;
        if (activity is null) return;

        var traceIdHex = activity.TraceId.ToHexString();   // 32 chars
        var spanIdHex = activity.SpanId.ToHexString();     // 16 chars

        // Lower 64 bits of the 128-bit W3C trace_id → DD's native trace_id
        // format. DD's agent log pipeline matches on this when the trace
        // is OTel-emitted with a 128-bit ID (the high 64 bits are stored
        // separately and the agent recombines them at correlation time).
        if (ulong.TryParse(traceIdHex.AsSpan(16), NumberStyles.HexNumber, CultureInfo.InvariantCulture, out var traceLow64) &&
            ulong.TryParse(spanIdHex,             NumberStyles.HexNumber, CultureInfo.InvariantCulture, out var spanId))
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
