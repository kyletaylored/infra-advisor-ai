using System.Collections.Concurrent;

namespace InfraAdvisor.AgentApi.Services;

// Thread-safe ring buffer of recent AI Guard evaluation outcomes for the
// admin diagnostics panel. AI Guard's HTTP API path sends no traces to
// Datadog — a hard platform limitation stated in DD's own docs, not a
// config gap (see DatadogAiGuardClient) — so without this the only way to
// know whether AI Guard is actually evaluating requests is grepping pod logs.
//
// Capped at MaxEntries; oldest entries fall off as new ones land.
public class AiGuardSubmissionLog
{
    private const int MaxEntries = 50;

    private readonly ConcurrentQueue<AiGuardSubmissionEntry> _entries = new();
    private long _totalEvaluated;
    private long _totalBlocked;
    private long _totalFailed;

    public void Record(AiGuardSubmissionEntry entry)
    {
        _entries.Enqueue(entry);
        Interlocked.Increment(ref _totalEvaluated);
        if (entry.Action is "DENY" or "ABORT") Interlocked.Increment(ref _totalBlocked);
        if (!entry.Success) Interlocked.Increment(ref _totalFailed);

        while (_entries.Count > MaxEntries && _entries.TryDequeue(out _))
        {
            // intentionally empty — drain until at cap
        }
    }

    public AiGuardSubmissionLogSnapshot Snapshot()
    {
        var recent = _entries.ToArray();
        return new AiGuardSubmissionLogSnapshot(
            TotalEvaluated: Interlocked.Read(ref _totalEvaluated),
            TotalBlocked: Interlocked.Read(ref _totalBlocked),
            TotalFailed: Interlocked.Read(ref _totalFailed),
            Recent: recent.Reverse().ToList());  // newest first for UI
    }
}

public record AiGuardSubmissionEntry(
    DateTimeOffset Timestamp,
    string Action,                // ALLOW | DENY | ABORT
    string? Reason,
    string? TraceIdDecimal,
    string? SpanIdDecimal,
    bool Success,                  // false = HTTP 4xx/5xx or exception (fails open to ALLOW)
    int DurationMs,
    string? Error);                // populated when Success=false

public record AiGuardSubmissionLogSnapshot(
    long TotalEvaluated,
    long TotalBlocked,
    long TotalFailed,
    List<AiGuardSubmissionEntry> Recent);
