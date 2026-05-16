using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;

namespace InfraAdvisor.AgentApi.Services;

// Thin HTTP client wrapping DD's LLM Observability evaluations API.
//
//   POST https://api.<site>/api/intake/llm-obs/v2/eval-metric
//
// Used for "external evaluations" — scores produced by code we control
// (MAF-style evaluators) that attach to existing OTel-emitted spans. Tagged
// source:otel per DD's OTel-instrumented-spans requirement (dd-otel.md).
//
// Designed to be fire-and-forget from the caller's perspective: every
// submission method catches its own exceptions and logs a warning. The
// agent loop must never block on eval submission failing — the eval signal
// is supplementary, not load-bearing.
//
// Disabled gracefully when DD_API_KEY isn't set (returns no-op task).
public class DatadogEvalsClient
{
    private readonly HttpClient _http;
    private readonly ILogger<DatadogEvalsClient> _logger;
    private readonly EvalSubmissionLog _log;
    private readonly string? _apiKey;
    private readonly string _site;
    private readonly string _mlApp;
    private readonly bool _enabled;

    public DatadogEvalsClient(
        HttpClient http,
        ILogger<DatadogEvalsClient> logger,
        EvalSubmissionLog log)
    {
        _http = http;
        _logger = logger;
        _log = log;
        _apiKey = Environment.GetEnvironmentVariable("DD_API_KEY");
        _site = Environment.GetEnvironmentVariable("DD_SITE") ?? "datadoghq.com";
        _mlApp = Environment.GetEnvironmentVariable("OTEL_SERVICE_NAME")
                 ?? "infra-advisor-agent-api-dotnet";
        _enabled = !string.IsNullOrWhiteSpace(_apiKey);

        if (!_enabled)
            _logger.LogWarning("DD_API_KEY not set; DatadogEvalsClient disabled — external evals will not be submitted.");
    }

    public bool Enabled => _enabled;
    public string MlApp => _mlApp;
    public string Site => _site;

    public Task SubmitBooleanAsync(
        string traceIdDecimal, string spanIdDecimal,
        string label, bool value,
        string? reasoning = null,
        IEnumerable<string>? extraTags = null,
        CancellationToken ct = default) =>
        SubmitAsync(traceIdDecimal, spanIdDecimal, "boolean", label,
            valueField: ("boolean_value", value),
            reasoning: reasoning, extraTags: extraTags, ct: ct);

    public Task SubmitScoreAsync(
        string traceIdDecimal, string spanIdDecimal,
        string label, double value,
        string? reasoning = null,
        IEnumerable<string>? extraTags = null,
        CancellationToken ct = default) =>
        SubmitAsync(traceIdDecimal, spanIdDecimal, "score", label,
            valueField: ("score_value", value),
            reasoning: reasoning, extraTags: extraTags, ct: ct);

    public Task SubmitCategoricalAsync(
        string traceIdDecimal, string spanIdDecimal,
        string label, string value,
        string? reasoning = null,
        IEnumerable<string>? extraTags = null,
        CancellationToken ct = default) =>
        SubmitAsync(traceIdDecimal, spanIdDecimal, "categorical", label,
            valueField: ("categorical_value", value),
            reasoning: reasoning, extraTags: extraTags, ct: ct);

    private async Task SubmitAsync(
        string traceIdDecimal, string spanIdDecimal,
        string metricType, string label,
        (string Key, object Value) valueField,
        string? reasoning,
        IEnumerable<string>? extraTags,
        CancellationToken ct)
    {
        if (!_enabled)
        {
            // Even when DD submission is disabled, record the attempt in
            // the diagnostic log so the admin panel can show "would have
            // submitted X but DD_API_KEY missing".
            _log.Record(new EvalSubmissionEntry(
                Timestamp: DateTimeOffset.UtcNow,
                Label: label,
                MetricType: metricType,
                Value: valueField.Value,
                Reasoning: TruncateForLog(reasoning),
                TraceIdDecimal: traceIdDecimal,
                SpanIdDecimal: spanIdDecimal,
                Success: false,
                DurationMs: 0,
                Error: "DD_API_KEY not set — submission skipped"));
            return;
        }

        var startedAt = DateTimeOffset.UtcNow;
        var success = false;
        string? error = null;
        try
        {
            // DD's eval-metric API uses JSON:API-style envelope. `metrics` is
            // an array so multiple evals can ship in one call — we send one
            // per request today for simpler error handling.
            var tags = new List<string> { "source:otel" };
            if (extraTags is not null) tags.AddRange(extraTags);

            var metric = new Dictionary<string, object?>
            {
                ["join_on"] = new
                {
                    span = new { trace_id = traceIdDecimal, span_id = spanIdDecimal },
                },
                ["metric_type"] = metricType,
                ["ml_app"] = _mlApp,
                ["timestamp_ms"] = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                ["label"] = label,
                [valueField.Key] = valueField.Value,
                ["tags"] = tags,
            };
            if (reasoning is not null) metric["reasoning"] = reasoning;

            var payload = new
            {
                data = new
                {
                    type = "evaluation_metric",
                    attributes = new { metrics = new[] { metric } },
                },
            };

            var url = $"https://api.{_site}/api/intake/llm-obs/v2/eval-metric";
            using var req = new HttpRequestMessage(HttpMethod.Post, url);
            req.Headers.TryAddWithoutValidation("DD-API-KEY", _apiKey);
            req.Content = new StringContent(
                JsonSerializer.Serialize(payload),
                Encoding.UTF8,
                new MediaTypeHeaderValue("application/json"));

            using var resp = await _http.SendAsync(req, ct);
            if (!resp.IsSuccessStatusCode)
            {
                var body = await resp.Content.ReadAsStringAsync(ct);
                error = $"HTTP {(int)resp.StatusCode}: {Truncate(body, 120)}";
                _logger.LogWarning(
                    "DD eval submission failed: {Status} {Label} — {Body}",
                    (int)resp.StatusCode, label, Truncate(body, 200));
            }
            else
            {
                success = true;
            }
        }
        catch (Exception ex)
        {
            error = $"{ex.GetType().Name}: {Truncate(ex.Message, 120)}";
            _logger.LogWarning("DD eval submission threw for {Label}: {Error}", label, ex.Message);
        }
        finally
        {
            var durationMs = (int)(DateTimeOffset.UtcNow - startedAt).TotalMilliseconds;
            _log.Record(new EvalSubmissionEntry(
                Timestamp: startedAt,
                Label: label,
                MetricType: metricType,
                Value: valueField.Value,
                Reasoning: TruncateForLog(reasoning),
                TraceIdDecimal: traceIdDecimal,
                SpanIdDecimal: spanIdDecimal,
                Success: success,
                DurationMs: durationMs,
                Error: error));
        }
    }

    private static string? TruncateForLog(string? reasoning) =>
        reasoning is null ? null : Truncate(reasoning, 240);

    private static string Truncate(string s, int max) =>
        s.Length <= max ? s : s[..max] + "…";
}
