namespace InfraAdvisor.AgentApi.Services.Evaluators;

// Deterministic evaluators score an already-captured agent response. Run
// in a background Task.Run from AgentService, scores POSTed to DD via
// DatadogEvalsClient and joined onto the invoke_agent span.
//
// Stay deterministic (regex, ordered-tool-walk, etc.) — LLM-as-judge
// evaluators belong in a separate ILlmJudge interface so the latency
// + cost characteristics don't bleed into the same plumbing.
public interface IResponseEvaluator
{
    // Stable identifier — becomes the `label` on the DD eval-metric record.
    // Filter dashboards / monitors by this. snake_case, no spaces.
    string Label { get; }

    EvalResult Evaluate(EvalInput input);
}

public record EvalInput(
    string Query,
    string Answer,
    IReadOnlyList<string> ToolsCalled,
    IReadOnlyList<string> Sources,
    string QueryDomain);

// One evaluator result. Discriminated by MetricType so the client picks the
// right DD field (boolean_value / score_value / categorical_value).
public record EvalResult(
    string MetricType,
    object Value,
    string? Reasoning = null);
