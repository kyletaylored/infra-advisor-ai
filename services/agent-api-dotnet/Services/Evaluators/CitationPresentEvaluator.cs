using System.Text.RegularExpressions;

namespace InfraAdvisor.AgentApi.Services.Evaluators;

// Boolean evaluator asserting system-prompt rule #1:
//   "Always cite the data source for factual claims"
//
// "Cited" = either (a) any source surfaced by ExtractSourcesFromResponse
// or (b) at least one domain-specific identifier in the answer text.
// Either path satisfies the rule; (b) catches cases where the model wrote
// a citation inline that didn't get parsed from tool result JSON.
//
// Short / chitchat answers (≤120 chars) are exempted — answering "hi" or
// asking a clarifying question shouldn't be marked as a missing citation.
// This mirrors what an annotator would do: low-effort responses bypass
// the citation rule because they're not making factual claims.
public class CitationPresentEvaluator : IResponseEvaluator
{
    public string Label => "citation_present";

    // Identifier shapes from the MCP tool catalog (system prompt rule #1
    // lists these explicitly). Conservative on false positives.
    private static readonly Regex IdPattern = new(
        @"\b(NBI[-\s]?\d+|PWSID[-\s]?\w+|EIA[-\s]?\d+|FEMA[-\s]?\w+-\d+|DR-\d+|EM-\d+|" +
        @"\d{13}|"  + // SAM.gov solicitation (13-char alphanumeric is a near miss; tighten if noisy)
        @"award[_\s]id|structure[_\s]number)\b",
        RegexOptions.IgnoreCase | RegexOptions.Compiled);

    public Task<EvalResult> EvaluateAsync(EvalInput input, CancellationToken ct)
    {
        if (input.Answer.Length < 120)
            return Task.FromResult(new EvalResult("boolean", true, "Exempt: short/clarifying response"));

        if (input.Sources.Count > 0)
            return Task.FromResult(new EvalResult("boolean", true,
                $"Tool-derived sources present: {string.Join(", ", input.Sources.Take(3))}"));

        if (IdPattern.IsMatch(input.Answer))
            return Task.FromResult(new EvalResult("boolean", true, "Inline identifier match in answer text"));

        return Task.FromResult(new EvalResult("boolean", false,
            "No tool-derived source and no domain-specific identifier (NBI / PWSID / EIA / FEMA / award_id) in answer"));
    }
}
