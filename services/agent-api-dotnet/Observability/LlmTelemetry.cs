using System.Diagnostics;

namespace InfraAdvisor.AgentApi.Observability;

public static class LlmTelemetry
{
    public static readonly ActivitySource ActivitySource =
        new("infra-advisor.llm", "1.0.0");

    public static Activity? StartLlmActivity(
        string modelName,
        string prompt,
        string taskType = "chat",
        string provider = "azure_openai",
        string? conversationId = null)
    {
        var activity = ActivitySource.StartActivity(
            $"gen_ai.{taskType}",
            ActivityKind.Client);

        if (activity is null) return null;

        activity.SetTag("gen_ai.operation.name", "chat");
        activity.SetTag("gen_ai.system", provider);
        activity.SetTag("gen_ai.request.model", modelName);
        activity.SetTag("gen_ai.prompt.0.role", "user");
        activity.SetTag("gen_ai.prompt.0.content", prompt);

        if (conversationId is not null)
            activity.SetTag("gen_ai.conversation.id", conversationId);

        return activity;
    }

    public static void EndLlmActivity(
        Activity? activity,
        string response,
        bool isSuccess,
        long latencyMs,
        int inputTokens = 0,
        int outputTokens = 0)
    {
        if (activity is null) return;

        activity.SetTag("gen_ai.completion.0.role", "assistant");
        activity.SetTag("gen_ai.completion.0.content", response);

        if (inputTokens > 0)
            activity.SetTag("gen_ai.usage.input_tokens", inputTokens);
        if (outputTokens > 0)
            activity.SetTag("gen_ai.usage.output_tokens", outputTokens);

        activity.SetTag("llm.latency_ms", latencyMs);
        activity.SetStatus(isSuccess ? ActivityStatusCode.Ok : ActivityStatusCode.Error);
        activity.Dispose();
    }
}
