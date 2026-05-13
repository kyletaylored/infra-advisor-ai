using System.Diagnostics;
using System.Globalization;

namespace InfraAdvisor.AgentApi.Services;

// Captures the invoke_agent span's IDs into an AsyncLocal so that code
// running AFTER agent.RunAsync (where Activity.Current has reverted to the
// HTTP server span) can still address the agent-kind span when submitting
// external evaluations to DD's eval-metric API.
//
// Populated by an ActivityListener in Program.cs that fires on activity
// start. Cleared between requests automatically by AsyncLocal scoping.
//
// DD's eval-metric API accepts decimal-string OR 32-char hex for trace_id
// and decimal-string for span_id. We capture both raw formats and let the
// caller pick the conversion it needs.
public static class AgentSpanContext
{
    private static readonly AsyncLocal<Captured?> _ctx = new();

    public static Captured? Current => _ctx.Value;

    public static void Capture(Activity activity)
    {
        var traceHex = activity.TraceId.ToString();
        var spanHex = activity.SpanId.ToString();
        _ctx.Value = new Captured(
            TraceIdHex: traceHex,
            SpanIdHex: spanHex,
            TraceIdDecimal: HexLowerToDecimal(traceHex),
            SpanIdDecimal: HexToDecimal(spanHex));
    }

    // 64-bit decimal from a hex string. For 32-char trace IDs we take the
    // low 16 hex chars (low 64 bits) to match DD APM's trace_id form.
    private static string HexLowerToDecimal(string hex)
    {
        if (hex.Length == 32) hex = hex[16..];
        return HexToDecimal(hex);
    }

    private static string HexToDecimal(string hex)
    {
        if (ulong.TryParse(hex, NumberStyles.HexNumber, CultureInfo.InvariantCulture, out var v))
            return v.ToString(CultureInfo.InvariantCulture);
        return hex;
    }

    public record Captured(
        string TraceIdHex,
        string SpanIdHex,
        string TraceIdDecimal,
        string SpanIdDecimal);
}
