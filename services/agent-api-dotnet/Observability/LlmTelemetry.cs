using System.Diagnostics;

namespace InfraAdvisor.AgentApi.Observability;

/// <summary>
/// Emits OpenInference-compatible LLM spans via the service's OTel ActivitySource.
/// Matches the semantic conventions expected by Datadog LLM Observability.
/// </summary>
public static class LlmTelemetry
{
    private static readonly ActivitySource Source =
        new(TelemetrySetup.ActivitySourceName);

    public static Activity? StartLlmActivity(
        string modelName,
        string prompt,
        string taskType,
        string provider)
    {
        var activity = Source.StartActivity($"llm.{taskType}", ActivityKind.Client);
        if (activity is null) return null;

        activity.SetTag("openinference.span.kind", "LLM");
        activity.SetTag("gen_ai.system", provider);
        activity.SetTag("gen_ai.request.model", modelName);
        activity.SetTag("llm.task_type", taskType);
        if (prompt.Length > 0)
            activity.SetTag("gen_ai.prompt.0.content",
                prompt.Length > 500 ? prompt[..500] : prompt);

        return activity;
    }

    public static void EndLlmActivity(
        Activity? activity,
        string response,
        bool isSuccess,
        long latencyMs)
    {
        if (activity is null) return;

        activity.SetTag("gen_ai.completion.0.content",
            response.Length > 500 ? response[..500] : response);
        activity.SetTag("llm.latency_ms", latencyMs);
        activity.SetTag("otel.status_code", isSuccess ? "OK" : "ERROR");
        activity.SetStatus(isSuccess ? ActivityStatusCode.Ok : ActivityStatusCode.Error);
    }
}
