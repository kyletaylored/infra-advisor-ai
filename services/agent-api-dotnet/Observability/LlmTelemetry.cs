using System.Diagnostics;
using System.Text.Json;

namespace InfraAdvisor.AgentApi.Observability;

// Datadog LLM Observability instrumentation via OTel OTLP.
// Spec: https://docs.datadoghq.com/llm_observability/instrumentation/otel_instrumentation
//
// How span kinds are determined (from spec):
//   gen_ai.operation.name = "invoke_agent" | "create_agent"  → agent
//   gen_ai.operation.name = "chat" | "text_completion" | ... → llm
//   gen_ai.operation.name = "execute_tool"                   → tool
//   gen_ai.operation.name = "embeddings" | "embedding"       → embedding
//   absent / unrecognised + dd.llmobs.span.kind = "workflow" → workflow
//
// Session linking: gen_ai.conversation.id → session_id in LLMObs
// Message format: gen_ai.input/output.messages must use the parts array:
//   [{"role":"user","parts":[{"type":"text","content":"..."}]}]
//   Flat {"role","content"} format produces "[No parts array - invalid otel message format]"
// Provider:       gen_ai.provider.name (primary), gen_ai.system (fallback)
// Filtered tags:  _dd.*, llm.*, ddtags, events — silently dropped by DD backend
// OTLP endpoint:  https://otlp.us3.datadoghq.com/v1/traces (US3)
//
// ActivitySource: callers pass the DI singleton (registered as LlmTelemetry.ActivitySource
// in Program.cs) so all spans share one instance and are captured together by the DD bridge.

public static class LlmTelemetry
{
    // The shared ActivitySource — registered in DI (Program.cs) as the singleton.
    // AgentService injects this and passes it to all LlmTelemetry methods.
    public static readonly ActivitySource ActivitySource =
        new(TelemetrySetup.ActivitySourceName, "1.0.0");

    private const string SpanKindTag = "dd.llmobs.span.kind";
    private const string MlApp = "infra-advisor";

    // ── Message format helpers ────────────────────────────────────────────────
    // DD LLMObs requires the parts array format. Flat {role, content} objects
    // produce "[No parts array - invalid otel message format]" in the UI.

    private static string SerializeMessages(string role, string content) =>
        JsonSerializer.Serialize(new[] {
            new {
                role,
                parts = new[] { new { type = "text", content } }
            }
        });

    private static void SetInputMessages(Activity activity, string role, string content)
    {
        var json = SerializeMessages(role, content);
        activity.SetTag("gen_ai.input.messages", json);
        activity.AddEvent(new ActivityEvent(
            "gen_ai.client.inference.operation.details",
            tags: new ActivityTagsCollection(new[] {
                KeyValuePair.Create<string, object?>("gen_ai.input.messages", json)
            })
        ));
    }

    private static void SetOutputMessages(Activity activity, string role, string content)
    {
        var json = SerializeMessages(role, content);
        activity.SetTag("gen_ai.output.messages", json);
        activity.AddEvent(new ActivityEvent(
            "gen_ai.client.inference.operation.details",
            tags: new ActivityTagsCollection(new[] {
                KeyValuePair.Create<string, object?>("gen_ai.output.messages", json)
            })
        ));
    }

    // ── Agent span ────────────────────────────────────────────────────────────

    public static Activity? StartAgentActivity(
        ActivitySource source,
        string agentName,
        string query,
        string sessionId)
    {
        if (TracingScope.IsSuppressed) return null;

        // Capture the APM (HTTP) trace ID before breaking the parent chain so we can
        // tag it for cross-product correlation between APM and LLMObs in the DD UI.
        var apmTraceId = Activity.Current?.TraceId.ToString();

        // Start invoke_agent as a NEW root trace. The DD bridge HTTP span (the natural
        // parent for UI requests) is captured for APM but is NOT exported to LLMObs.
        // Keeping it as parent makes invoke_agent appear with parentSpanId pointing to
        // a span that DD LLMObs cannot find in the OTLP batch — symptom: the OTLP POST
        // returns 202 Accepted but the trace never appears in the LLMObs UI. Kafka
        // traces work because eval.agent_run is a true root with no parent.
        // Fix: pass an ActivityContext with a fresh TraceId and zero SpanId so the new
        // activity is a true root in the OTLP export (parentSpanId absent in the proto).
        var rootContext = new ActivityContext(
            ActivityTraceId.CreateRandom(),
            default,
            ActivityTraceFlags.Recorded,
            isRemote: false);

        var activity = source.StartActivity(
            "invoke_agent",
            ActivityKind.Internal,
            rootContext);
        if (activity is null) return null;

        activity.SetTag("gen_ai.operation.name", "invoke_agent");
        activity.SetTag("gen_ai.agent.name", agentName);
        activity.SetTag("gen_ai.conversation.id", sessionId);
        activity.SetTag(SpanKindTag, "agent");
        activity.SetTag("ml_app", MlApp);
        if (!string.IsNullOrEmpty(apmTraceId))
            activity.SetTag("apm.trace_id", apmTraceId);
        SetInputMessages(activity, "user", query);

        return activity;
    }

    public static void EndAgentActivity(Activity? activity, string answer, bool isSuccess)
    {
        if (activity is null) return;
        SetOutputMessages(activity, "assistant", answer.Length > 4000 ? answer[..4000] : answer);
        activity.SetStatus(isSuccess ? ActivityStatusCode.Ok : ActivityStatusCode.Error);
    }

    // ── Workflow span tag helper ───────────────────────────────────────────────

    public static void TagWorkflow(Activity? activity, string sessionId)
    {
        if (activity is null) return;
        activity.SetTag("gen_ai.conversation.id", sessionId);
        activity.SetTag(SpanKindTag, "workflow");
        activity.SetTag("ml_app", MlApp);
    }

    // ── LLM span ──────────────────────────────────────────────────────────────

    public static Activity? StartLlmActivity(
        ActivitySource source,
        string modelName,
        string prompt,
        string sessionId,
        string operation = "chat",
        string provider = "azure_openai",
        string? name = null)
    {
        if (TracingScope.IsSuppressed) return null;

        var activity = source.StartActivity(
            name ?? $"{operation} {modelName}",
            ActivityKind.Client);
        if (activity is null) return null;

        activity.SetTag("gen_ai.operation.name", operation);
        activity.SetTag("gen_ai.provider.name", provider);
        activity.SetTag("gen_ai.system", provider);
        activity.SetTag("gen_ai.request.model", modelName);
        activity.SetTag("gen_ai.conversation.id", sessionId);
        activity.SetTag(SpanKindTag, "llm");
        activity.SetTag("ml_app", MlApp);
        SetInputMessages(activity, "user", prompt);

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

        SetOutputMessages(activity, "assistant", response);

        if (inputTokens > 0) activity.SetTag("gen_ai.usage.input_tokens", inputTokens);
        if (outputTokens > 0) activity.SetTag("gen_ai.usage.output_tokens", outputTokens);
        activity.SetTag("gen_ai.response.finish_reasons", JsonSerializer.Serialize(new[] { finishReason }));
        activity.SetTag("gen_ai.latency_ms", latencyMs);

        activity.SetStatus(isSuccess ? ActivityStatusCode.Ok : ActivityStatusCode.Error);
        activity.Dispose();
    }

    // ── Tool span ─────────────────────────────────────────────────────────────
    // gen_ai.operation.name = "execute_tool" → "tool" span kind in LLMObs.
    // Created in agent-api-dotnet around each _mcpClient.InvokeToolAsync call
    // so tool executions appear as first-class LLMObs spans instead of span links
    // from the separate mcp-server-dotnet service.

    public static Activity? StartToolActivity(
        ActivitySource source,
        string toolName,
        string inputJson,
        string sessionId)
    {
        if (TracingScope.IsSuppressed) return null;

        var activity = source.StartActivity($"execute_tool {toolName}", ActivityKind.Internal);
        if (activity is null) return null;

        activity.SetTag("gen_ai.operation.name", "execute_tool");
        activity.SetTag("gen_ai.tool.name", toolName);
        activity.SetTag("gen_ai.conversation.id", sessionId);
        activity.SetTag(SpanKindTag, "tool");
        activity.SetTag("ml_app", MlApp);
        SetInputMessages(activity, "tool", inputJson);

        return activity;
    }

    public static void EndToolActivity(Activity? activity, string result, bool isSuccess)
    {
        if (activity is null) return;
        SetOutputMessages(activity, "tool", result.Length > 2000 ? result[..2000] : result);
        activity.SetStatus(isSuccess ? ActivityStatusCode.Ok : ActivityStatusCode.Error);
        activity.Dispose();
    }
}
