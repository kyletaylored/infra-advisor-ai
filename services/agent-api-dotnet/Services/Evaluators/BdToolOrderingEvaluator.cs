namespace InfraAdvisor.AgentApi.Services.Evaluators;

// Boolean evaluator asserting system-prompt rule #4:
//   "For BD queries, always call get_contract_awards before
//    get_procurement_opportunities — understanding who won similar work
//    informs positioning for open opportunities."
//
// Trivially pass when neither tool was called (rule doesn't apply) or only
// one was called (no ordering to violate). Fail only when both tools were
// called AND get_procurement_opportunities came first.
public class BdToolOrderingEvaluator : IResponseEvaluator
{
    public string Label => "bd_tool_ordering";

    private const string AwardsTool = "get_contract_awards";
    private const string OppsTool   = "get_procurement_opportunities";

    public Task<EvalResult> EvaluateAsync(EvalInput input, CancellationToken ct)
    {
        var awardsIdx = IndexOf(input.ToolsCalled, AwardsTool);
        var oppsIdx   = IndexOf(input.ToolsCalled, OppsTool);

        if (awardsIdx < 0 && oppsIdx < 0)
            return Task.FromResult(new EvalResult("boolean", true, "Rule N/A: neither BD tool called"));

        if (awardsIdx < 0 || oppsIdx < 0)
            return Task.FromResult(new EvalResult("boolean", true,
                "Rule N/A: only one BD tool called (ordering doesn't apply)"));

        return Task.FromResult(awardsIdx < oppsIdx
            ? new EvalResult("boolean", true,
                $"Correct order: {AwardsTool} at #{awardsIdx} before {OppsTool} at #{oppsIdx}")
            : new EvalResult("boolean", false,
                $"Wrong order: {OppsTool} at #{oppsIdx} called before {AwardsTool} at #{awardsIdx}"));
    }

    private static int IndexOf(IReadOnlyList<string> list, string item)
    {
        for (var i = 0; i < list.Count; i++)
            if (string.Equals(list[i], item, StringComparison.Ordinal)) return i;
        return -1;
    }
}
