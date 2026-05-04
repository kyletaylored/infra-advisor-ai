using System.Text.Json.Serialization;

namespace InfraAdvisor.AgentApi.Models;

public record QueryRequest(
    [property: JsonPropertyName("query")] string Query,
    [property: JsonPropertyName("session_id")] string? SessionId = null,
    [property: JsonPropertyName("model")] string? Model = null
);
