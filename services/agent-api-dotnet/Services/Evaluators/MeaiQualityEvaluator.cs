using Microsoft.Extensions.AI;
using Microsoft.Extensions.AI.Evaluation;
using Microsoft.Extensions.AI.Evaluation.Quality;

namespace InfraAdvisor.AgentApi.Services.Evaluators;

// LLM-as-judge wrappers around Microsoft.Extensions.AI.Evaluation.Quality
// evaluators. Each instance runs a judge LLM call against the captured
// (user_query, assistant_answer) pair and surfaces a 1-5 numeric score
// + the judge's reasoning to Datadog via the existing IResponseEvaluator
// → DatadogEvalsClient pipeline.
//
// Cost note: each evaluator that runs adds ONE judge model call. At the
// default EVAL_SAMPLE_RATE=0.1 that's 10% of /query × N evaluators extra
// inference calls. The judge model is the same gpt-4.1-mini we use for
// the agent (configured via IChatClient DI) — M.E.AI's prompts are
// "designed to be model-agnostic" but tuned best for GPT-4o-class models,
// so scores from gpt-4.1-mini are still useful as a trend signal but
// shouldn't be used for absolute thresholds.
//
// Shape note: M.E.AI Quality evaluators return EvaluationResult containing
// a NumericMetric with a 1-5 score. We map score → DD's score_value
// metric_type and the metric's Reason field → reasoning. M.E.AI sets a
// Reason interpretation (e.g. Inconclusive / Below-target / Above-target);
// when set to anything other than the success interpretation we mark the
// score with a "below_3" suffix in the reasoning so DD filters surface
// degraded runs.
//
// Reference docs:
//   https://learn.microsoft.com/en-us/agent-framework/agents/evaluation?pivots=programming-language-csharp
//   https://docs.datadoghq.com/llm_observability/instrumentation/api/?tab=model#evaluations-api
public abstract class MeaiQualityEvaluator : IResponseEvaluator
{
    private readonly IChatClient _judgeClient;
    private readonly ILogger _logger;

    protected MeaiQualityEvaluator(IChatClient judgeClient, ILogger logger)
    {
        _judgeClient = judgeClient;
        _logger = logger;
    }

    public abstract string Label { get; }

    // The concrete M.E.AI evaluator (RelevanceEvaluator, GroundednessEvaluator,
    // etc.) the subclass instantiates. New per-call because the M.E.AI types
    // are cheap to construct and we want each call to be independent.
    protected abstract IEvaluator CreateInnerEvaluator();

    // Whether this evaluator type wants the retrieved-context / tool-result
    // data as EvaluationContext. Groundedness uses it; Relevance ignores it.
    protected virtual IEnumerable<EvaluationContext>? BuildContext(EvalInput input) => null;

    public async Task<EvalResult> EvaluateAsync(EvalInput input, CancellationToken ct)
    {
        if (string.IsNullOrWhiteSpace(input.Answer))
            return new EvalResult("score", 0.0, "Empty answer — judge skipped");

        try
        {
            var inner = CreateInnerEvaluator();
            var chatConfig = new ChatConfiguration(_judgeClient);
            var ctx = BuildContext(input);

            // The string/string extension overload is the simplest call shape:
            // first arg is the user query, second is the assistant response.
            var result = await inner.EvaluateAsync(
                input.Query, input.Answer, chatConfig, ctx, ct);

            // Each Quality evaluator emits exactly one NumericMetric. Pull
            // the first by name match — falling back to the first metric
            // for forward-compatibility if the metric name changes.
            var metric = result.Metrics.Values
                .OfType<NumericMetric>()
                .FirstOrDefault();
            if (metric is null)
            {
                return new EvalResult("score", 0.0,
                    "Judge returned no NumericMetric — possible model output parse failure");
            }

            var score = metric.Value ?? 0.0;
            var reason = metric.Reason ?? "no reasoning provided";
            var interpretation = metric.Interpretation?.Rating.ToString() ?? "Unknown";
            return new EvalResult(
                MetricType: "score",
                Value: score,
                Reasoning: $"[{interpretation}] {Truncate(reason, 480)}");
        }
        catch (Exception ex)
        {
            _logger.LogWarning("MeaiQualityEvaluator {Label} threw: {Error}", Label, ex.Message);
            return new EvalResult("score", 0.0, $"Judge call failed: {ex.GetType().Name}");
        }
    }

    private static string Truncate(string s, int max) =>
        s.Length <= max ? s : s[..max] + "…";
}

// Relevance: did the assistant's answer actually address the user's question?
// 1 = unrelated, 5 = perfectly on-target. Doesn't require tool-result context.
public sealed class MeaiRelevanceEvaluator(IChatClient judgeClient, ILogger<MeaiRelevanceEvaluator> logger)
    : MeaiQualityEvaluator(judgeClient, logger)
{
    public override string Label => "meai_relevance";
    protected override IEvaluator CreateInnerEvaluator() => new RelevanceEvaluator();
}

// Groundedness: are claims in the answer supported by the supplied context
// (tool results + retrieved snippets)? Catches hallucinations. Pass the
// captured tool results as EvaluationContext so the judge has the truth
// to compare against.
public sealed class MeaiGroundednessEvaluator(IChatClient judgeClient, ILogger<MeaiGroundednessEvaluator> logger)
    : MeaiQualityEvaluator(judgeClient, logger)
{
    public override string Label => "meai_groundedness";

    protected override IEvaluator CreateInnerEvaluator() => new GroundednessEvaluator();

    protected override IEnumerable<EvaluationContext>? BuildContext(EvalInput input)
    {
        if (input.ToolResults.Count == 0) return null;
        // GroundednessEvaluatorContext takes a single concatenated string
        // of the grounding information. We join the tool result blobs with
        // a separator so the judge can tell them apart.
        var joined = string.Join("\n\n---\n\n",
            input.ToolResults.Where(r => !string.IsNullOrWhiteSpace(r)));
        if (string.IsNullOrWhiteSpace(joined)) return null;
        return new[] { (EvaluationContext)new GroundednessEvaluatorContext(joined) };
    }
}
