using System.Text.Json.Serialization;

namespace InfraAdvisor.AgentApi.Models;

public record QueryResponse(
    [property: JsonPropertyName("answer")] string Answer,
    [property: JsonPropertyName("sources")] List<string> Sources,
    [property: JsonPropertyName("trace_id")] string? TraceId,
    [property: JsonPropertyName("span_id")] string? SpanId,
    [property: JsonPropertyName("session_id")] string SessionId,
    [property: JsonPropertyName("model")] string Model
);
