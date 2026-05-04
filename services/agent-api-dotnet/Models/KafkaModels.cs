using System.Text.Json.Serialization;

namespace InfraAdvisor.AgentApi.Models;

public record KafkaQueryEvent(
    [property: JsonPropertyName("query_id")] string QueryId,
    [property: JsonPropertyName("query")] string Query,
    [property: JsonPropertyName("session_id")] string SessionId,
    [property: JsonPropertyName("corpus_type")] string CorpusType,
    [property: JsonPropertyName("domain")] string Domain
);

public record KafkaEvalResult(
    [property: JsonPropertyName("session_id")] string SessionId,
    [property: JsonPropertyName("query_id")] string QueryId,
    [property: JsonPropertyName("query")] string Query,
    [property: JsonPropertyName("answer")] string Answer,
    [property: JsonPropertyName("sources")] List<string> Sources,
    [property: JsonPropertyName("tools_called")] List<string> ToolsCalled,
    [property: JsonPropertyName("faithfulness_score")] double? FaithfulnessScore,
    [property: JsonPropertyName("latency_ms")] double LatencyMs,
    [property: JsonPropertyName("corpus_type")] string CorpusType,
    [property: JsonPropertyName("domain")] string Domain
);
