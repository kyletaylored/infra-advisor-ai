namespace InfraAdvisor.AgentApi.Services.Evaluators;

// Evaluators score an already-captured agent response. Run in a background
// Task.Run from AgentService; scores POSTed to DD via DatadogEvalsClient
// and joined onto the invoke_agent span at the existing EVAL_SAMPLE_RATE.
//
// Two flavors implement this interface:
//   - Deterministic (regex / list walks / ordering checks) — wrap their
//     synchronous logic in Task.FromResult.
//   - LLM-as-judge (Microsoft.Extensions.AI.Evaluation.Quality) — awaits a
//     judge model call inside EvaluateAsync. These add latency and a
//     billable judge call; they only run on the same EVAL_SAMPLE_RATE
//     fraction of traffic.
public interface IResponseEvaluator
{
    // Stable identifier — becomes the `label` on the DD eval-metric record.
    // Filter dashboards / monitors by this. snake_case, no spaces.
    string Label { get; }

    Task<EvalResult> EvaluateAsync(EvalInput input, CancellationToken ct);
}

public record EvalInput(
    string Query,
    string Answer,
    IReadOnlyList<string> ToolsCalled,
    // Raw tool RESULTS captured from FunctionResultContent in the agent
    // response. LLM-judge evaluators (Groundedness) need this to verify
    // claims in the answer against the actual data the tools returned.
    // Deterministic evaluators can ignore it.
    IReadOnlyList<string> ToolResults,
    IReadOnlyList<string> Sources,
    string QueryDomain);

// One evaluator result. Discriminated by MetricType so the client picks the
// right DD field (boolean_value / score_value / categorical_value).
public record EvalResult(
    string MetricType,
    object Value,
    string? Reasoning = null);
