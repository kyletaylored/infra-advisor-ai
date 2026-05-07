using System.Diagnostics;

namespace InfraAdvisor.AgentApi.Observability;

public static class LlmTelemetry
{
    // All LLM spans share the service ActivitySource so the DD bridge (APM via :8126)
    // and the non-global OTLP provider (LLMObs via dd-otlp-source=llmobs) both capture
    // them in the same trace without needing a separate AddSource() registration.
    public static readonly ActivitySource ActivitySource =
        new(TelemetrySetup.ActivitySourceName, "1.0.0");

    // Tells Datadog LLM Observability what kind of ML span this is.
    // Values: agent | workflow | task | llm | tool | embedding | retrieval
    // https://docs.datadoghq.com/llm_observability/terms/#llm-span
    private const string SpanKindTag = "dd.llmobs.span.kind";
    private const string MlApp = "infra-advisor";

    // ── Agent span (top-level — wraps the entire query lifecycle) ─────────────

    public static Activity? StartAgentActivity(string agentName, string query, string sessionId)
    {
        var activity = ActivitySource.StartActivity("run_agent", ActivityKind.Internal);
        if (activity is null) return null;

        // OTel GenAI agent span attributes
        // https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-agent-spans/
        activity.SetTag("gen_ai.operation.name", "run_agent");
        activity.SetTag("gen_ai.agent.name", agentName);

        // DD LLMObs span kind + session
        activity.SetTag(SpanKindTag, "agent");
        activity.SetTag("session.id", sessionId);
        activity.SetTag("ml_app", MlApp);

        // Input as span event (OTel GenAI spec)
        AddEvent(activity, "gen_ai.user.message", ("content", query), ("role", "user"));

        return activity;
    }

    public static void EndAgentActivity(Activity? activity, string answer, bool isSuccess)
    {
        if (activity is null) return;

        // Output as span event
        AddEvent(activity, "gen_ai.assistant.message",
            ("content", answer.Length > 2000 ? answer[..2000] : answer),
            ("role", "assistant"));

        activity.SetStatus(isSuccess ? ActivityStatusCode.Ok : ActivityStatusCode.Error);
        // disposed by caller's using block
    }

    // ── Workflow span (router, specialist — orchestration layers) ─────────────

    public static void TagWorkflow(Activity? activity, string sessionId)
    {
        if (activity is null) return;
        activity.SetTag(SpanKindTag, "workflow");
        activity.SetTag("session.id", sessionId);
        activity.SetTag("ml_app", MlApp);
    }

    // ── LLM span (individual Azure OpenAI CompleteChatAsync call) ─────────────

    public static Activity? StartLlmActivity(
        string modelName,
        string prompt,
        string sessionId,
        string operation = "chat",
        string provider = "azure_openai",
        string? name = null)
    {
        var activity = ActivitySource.StartActivity(
            name ?? $"{operation} {modelName}",
            ActivityKind.Client);
        if (activity is null) return null;

        // OTel GenAI LLM span attributes
        // https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-spans/
        activity.SetTag("gen_ai.operation.name", operation);
        activity.SetTag("gen_ai.system", provider);
        activity.SetTag("gen_ai.request.model", modelName);

        // DD LLMObs span kind + session
        activity.SetTag(SpanKindTag, "llm");
        activity.SetTag("session.id", sessionId);
        activity.SetTag("ml_app", MlApp);

        // Input — attribute form (Datadog LLMObs OTLP ingestion)
        activity.SetTag("gen_ai.prompt.0.role", "user");
        activity.SetTag("gen_ai.prompt.0.content", prompt);

        // Input — event form (OTel GenAI semantic conventions)
        AddEvent(activity, "gen_ai.user.message", ("content", prompt), ("role", "user"));

        return activity;
    }

    public static void EndLlmActivity(
        Activity? activity,
        string response,
        bool isSuccess,
        long latencyMs,
        int inputTokens = 0,
        int outputTokens = 0,
        string finishReason = "stop")
    {
        if (activity is null) return;

        // Output — attribute form (Datadog LLMObs OTLP ingestion)
        activity.SetTag("gen_ai.completion.0.role", "assistant");
        activity.SetTag("gen_ai.completion.0.content", response);

        // Output — event form (OTel GenAI semantic conventions)
        AddEvent(activity, "gen_ai.choice",
            ("index", (object)0),
            ("finish_reason", finishReason),
            ("message.role", "assistant"),
            ("message.content", response));

        if (inputTokens > 0) activity.SetTag("gen_ai.usage.input_tokens", inputTokens);
        if (outputTokens > 0) activity.SetTag("gen_ai.usage.output_tokens", outputTokens);

        activity.SetTag("llm.latency_ms", latencyMs);
        activity.SetStatus(isSuccess ? ActivityStatusCode.Ok : ActivityStatusCode.Error);
        activity.Dispose();
    }

    // ── Helper ────────────────────────────────────────────────────────────────

    private static void AddEvent(Activity activity, string name, params (string key, object value)[] attrs)
    {
        var tags = new ActivityTagsCollection();
        foreach (var (k, v) in attrs) tags[k] = v;
        activity.AddEvent(new ActivityEvent(name, tags: tags));
    }
}
