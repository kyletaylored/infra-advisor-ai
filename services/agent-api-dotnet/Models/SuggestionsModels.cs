using System.Text.Json.Serialization;

namespace InfraAdvisor.AgentApi.Models;

public record SuggestionsRequest(
    [property: JsonPropertyName("query")] string Query,
    [property: JsonPropertyName("answer")] string Answer,
    [property: JsonPropertyName("sources")] List<string>? Sources = null,
    [property: JsonPropertyName("session_id")] string? SessionId = null
);

public record SuggestionItem(
    [property: JsonPropertyName("label")] string Label,
    [property: JsonPropertyName("query")] string Query
);

public record SuggestionsResponse(
    [property: JsonPropertyName("suggestions")] List<SuggestionItem> Suggestions
);
