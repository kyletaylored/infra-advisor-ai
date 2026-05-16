using System.Collections.Concurrent;

namespace InfraAdvisor.AgentApi.Services;

// Thread-safe ring buffer of recent eval-submission outcomes for the
// admin diagnostics panel. The eval pipeline is fire-and-forget at the
// AgentService.ScheduleEvaluations level, so without this we have no
// in-process visibility into whether evals are actually firing — only
// the DD UI sees the results.
//
// Capped at MaxEntries; oldest entries fall off as new ones land.
// Concurrent access from N background eval tasks is safe via the
// ConcurrentQueue + Interlocked count.
public class EvalSubmissionLog
{
    private const int MaxEntries = 50;

    private readonly ConcurrentQueue<EvalSubmissionEntry> _entries = new();
    private long _totalSubmitted;
    private long _totalFailed;

    public void Record(EvalSubmissionEntry entry)
    {
        _entries.Enqueue(entry);
        Interlocked.Increment(ref _totalSubmitted);
        if (!entry.Success) Interlocked.Increment(ref _totalFailed);

        // Trim from the front to keep the buffer bounded.
        while (_entries.Count > MaxEntries && _entries.TryDequeue(out _))
        {
            // intentionally empty — drain until at cap
        }
    }

    public EvalSubmissionLogSnapshot Snapshot()
    {
        // ToArray on ConcurrentQueue is a thread-safe O(n) snapshot.
        var recent = _entries.ToArray();
        return new EvalSubmissionLogSnapshot(
            TotalSubmitted: Interlocked.Read(ref _totalSubmitted),
            TotalFailed: Interlocked.Read(ref _totalFailed),
            Recent: recent.Reverse().ToList());  // newest first for UI
    }
}

public record EvalSubmissionEntry(
    DateTimeOffset Timestamp,
    string Label,
    string MetricType,           // boolean | score | categorical
    object? Value,                // bool/double/string depending on metricType
    string? Reasoning,            // truncated to 240 chars for the log
    string? TraceIdDecimal,
    string? SpanIdDecimal,
    bool Success,                 // false = HTTP 4xx/5xx or exception
    int DurationMs,
    string? Error);               // populated when Success=false

public record EvalSubmissionLogSnapshot(
    long TotalSubmitted,
    long TotalFailed,
    List<EvalSubmissionEntry> Recent);
