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
// Input/output:   gen_ai.input.messages / gen_ai.output.messages (JSON arrays) — preferred
//                 Belt-and-suspenders: also emitted as gen_ai.client.inference.operation.details span event
// Provider:       gen_ai.provider.name (primary), gen_ai.system (fallback)
// Filtered tags:  _dd.*, llm.*, ddtags, events — silently dropped by DD backend
// OTLP endpoint:  https://otlp.us3.datadoghq.com/v1/traces (US3)
//
// ActivitySource: callers pass the DI singleton ActivitySource so all spans share
// one instance. Two ActivitySource instances with the same name can produce
// disconnected trace contexts in the DD bridge.

public static class LlmTelemetry
{
    // The shared ActivitySource — registered in DI (Program.cs) and injected into
    // AgentService. All LlmTelemetry methods receive this instance as a parameter
    // so agent, router, specialist, and LLM spans all belong to the same trace.
    public static readonly ActivitySource ActivitySource =
        new(TelemetrySetup.ActivitySourceName, "1.0.0");

    // Belt-and-suspenders span kind tag — also read by DD LLMObs for spans that
    // don't match a recognised gen_ai.operation.name.
    // https://docs.datadoghq.com/llm_observability/terms/#llm-span
    private const string SpanKindTag = "dd.llmobs.span.kind";
    private const string MlApp = "infra-advisor";

    // ── Agent span ────────────────────────────────────────────────────────────
    // Wraps the entire query lifecycle. gen_ai.operation.name = "invoke_agent"
    // maps this to the "agent" span kind in DD LLMObs.

    public static Activity? StartAgentActivity(
        ActivitySource source,
        string agentName,
        string query,
        string sessionId)
    {
        // Span name matches gen_ai.operation.name per OTel GenAI agent spans spec.
        var activity = source.StartActivity("invoke_agent", ActivityKind.Internal);
        if (activity is null) return null;

        // Required: operation name drives span kind resolution in LLMObs.
        // "invoke_agent" → agent kind. "run_agent" is NOT in the recognised list.
        activity.SetTag("gen_ai.operation.name", "invoke_agent");
        activity.SetTag("gen_ai.agent.name", agentName);

        // gen_ai.conversation.id is the spec attribute that maps to session_id.
        // Plain session.id is a non-gen_ai tag and becomes a key:value tag only.
        activity.SetTag("gen_ai.conversation.id", sessionId);
        activity.SetTag(SpanKindTag, "agent");
        activity.SetTag("ml_app", MlApp);

        // Input: gen_ai.input.messages JSON array — preferred direct attribute.
        // Also emitted as span event for belt-and-suspenders compatibility.
        var inputJson = JsonSerializer.Serialize(new[] { new { role = "user", content = query } });
        activity.SetTag("gen_ai.input.messages", inputJson);
        activity.AddEvent(new ActivityEvent(
            "gen_ai.client.inference.operation.details",
            tags: new ActivityTagsCollection(new[] {
                KeyValuePair.Create<string, object?>("gen_ai.input.messages", inputJson)
            })
        ));

        return activity;
    }

    public static void EndAgentActivity(Activity? activity, string answer, bool isSuccess)
    {
        if (activity is null) return;

        // Output: gen_ai.output.messages JSON array — preferred direct attribute.
        // Also emitted as span event for belt-and-suspenders compatibility.
        var outputJson = JsonSerializer.Serialize(new[] {
            new { role = "assistant", content = answer.Length > 4000 ? answer[..4000] : answer }
        });
        activity.SetTag("gen_ai.output.messages", outputJson);
        activity.AddEvent(new ActivityEvent(
            "gen_ai.client.inference.operation.details",
            tags: new ActivityTagsCollection(new[] {
                KeyValuePair.Create<string, object?>("gen_ai.output.messages", outputJson)
            })
        ));

        activity.SetStatus(isSuccess ? ActivityStatusCode.Ok : ActivityStatusCode.Error);
        // Disposed by the caller's using block; do NOT call Dispose() here.
    }

    // ── Workflow span tag helper ───────────────────────────────────────────────
    // Router and specialist spans use the default ActivitySource.StartActivity name.
    // No recognised gen_ai.operation.name → DD LLMObs falls back to "workflow" kind.
    // dd.llmobs.span.kind = "workflow" is also set as explicit hint.

    public static void TagWorkflow(Activity? activity, string sessionId)
    {
        if (activity is null) return;
        activity.SetTag("gen_ai.conversation.id", sessionId);
        activity.SetTag(SpanKindTag, "workflow");
        activity.SetTag("ml_app", MlApp);
    }

    // ── LLM span ──────────────────────────────────────────────────────────────
    // One per CompleteChatAsync call. gen_ai.operation.name = "chat" maps to "llm"
    // span kind. Both gen_ai.provider.name (primary) and gen_ai.system (fallback)
    // are set; DD reads whichever is present.

    public static Activity? StartLlmActivity(
        ActivitySource source,
        string modelName,
        string prompt,
        string sessionId,
        string operation = "chat",
        string provider = "azure_openai",
        string? name = null)
    {
        var activity = source.StartActivity(
            name ?? $"{operation} {modelName}",
            ActivityKind.Client);
        if (activity is null) return null;

        // gen_ai.operation.name: "chat" → "llm" span kind in DD LLMObs.
        activity.SetTag("gen_ai.operation.name", operation);

        // gen_ai.provider.name is the primary provider attribute (gen_ai.system is fallback).
        activity.SetTag("gen_ai.provider.name", provider);
        activity.SetTag("gen_ai.system", provider);

        activity.SetTag("gen_ai.request.model", modelName);

        // Session: gen_ai.conversation.id → session_id in LLMObs.
        activity.SetTag("gen_ai.conversation.id", sessionId);
        activity.SetTag(SpanKindTag, "llm");
        activity.SetTag("ml_app", MlApp);

        // Input: gen_ai.input.messages JSON array — preferred direct attribute.
        // NOT gen_ai.prompt.0.role / gen_ai.prompt.0.content — not read by OTLP ingestion.
        // Also emitted as span event for belt-and-suspenders compatibility.
        var inputJson = JsonSerializer.Serialize(new[] { new { role = "user", content = prompt } });
        activity.SetTag("gen_ai.input.messages", inputJson);
        activity.AddEvent(new ActivityEvent(
            "gen_ai.client.inference.operation.details",
            tags: new ActivityTagsCollection(new[] {
                KeyValuePair.Create<string, object?>("gen_ai.input.messages", inputJson)
            })
        ));

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

        // Output: gen_ai.output.messages JSON array — preferred direct attribute.
        // NOT gen_ai.completion.0.role / gen_ai.completion.0.content.
        // Also emitted as span event for belt-and-suspenders compatibility.
        var outputJson = JsonSerializer.Serialize(new[] { new { role = "assistant", content = response } });
        activity.SetTag("gen_ai.output.messages", outputJson);
        activity.AddEvent(new ActivityEvent(
            "gen_ai.client.inference.operation.details",
            tags: new ActivityTagsCollection(new[] {
                KeyValuePair.Create<string, object?>("gen_ai.output.messages", outputJson)
            })
        ));

        // Token counts — spec attribute names (gen_ai.usage.*).
        if (inputTokens > 0) activity.SetTag("gen_ai.usage.input_tokens", inputTokens);
        if (outputTokens > 0) activity.SetTag("gen_ai.usage.output_tokens", outputTokens);

        // gen_ai.response.finish_reasons is an array per spec.
        activity.SetTag("gen_ai.response.finish_reasons", JsonSerializer.Serialize(new[] { finishReason }));

        // NOTE: do NOT use "llm.*" prefix — that namespace is silently filtered by DD backend.
        activity.SetTag("gen_ai.latency_ms", latencyMs);

        activity.SetStatus(isSuccess ? ActivityStatusCode.Ok : ActivityStatusCode.Error);
        activity.Dispose();
    }
}
