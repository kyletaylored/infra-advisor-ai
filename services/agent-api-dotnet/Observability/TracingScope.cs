namespace InfraAdvisor.AgentApi.Observability;

// AsyncLocal flag for suppressing ActivitySource span creation in a scoped region.
// Used by KafkaConsumerService to disable eval-loop tracing entirely when
// KAFKA_TRACING_ENABLED=false — prevents the high-frequency eval messages
// from polluting the APM and LLMObs trace interfaces.
//
// Suppression is checked at every StartActivity call site (LlmTelemetry methods
// and direct ActivitySource.StartActivity calls in AgentService). When suppressed,
// no Activity is created, so the DD bridge has nothing to capture for APM and the
// non-global OTLP provider has nothing to export to LLMObs.
public static class TracingScope
{
    private static readonly AsyncLocal<bool> _suppressed = new();

    public static bool IsSuppressed => _suppressed.Value;

    public static IDisposable Suppress()
    {
        _suppressed.Value = true;
        return new ResetScope();
    }

    private sealed class ResetScope : IDisposable
    {
        public void Dispose() => _suppressed.Value = false;
    }
}
