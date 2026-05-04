using System.Text.Json.Serialization;

namespace InfraAdvisor.AgentApi.Models;

public record FeedbackRequest(
    [property: JsonPropertyName("trace_id")] string TraceId,
    [property: JsonPropertyName("span_id")] string SpanId,
    [property: JsonPropertyName("rating")] string Rating,
    [property: JsonPropertyName("session_id")] string? SessionId = null
);
