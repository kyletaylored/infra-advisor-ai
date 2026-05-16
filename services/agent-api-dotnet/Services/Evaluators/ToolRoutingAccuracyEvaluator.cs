using System.Text.RegularExpressions;

namespace InfraAdvisor.AgentApi.Services.Evaluators;

// Asserts that for a recognizable query pattern, the agent invoked the
// expected MCP tool. Catches the "agent picked the wrong tool" regression
// that the enriched descriptions / few-shot examples / grounded
// suggestions in commit 99b05d0 + the few-shot prompt change set out to
// fix. If those mitigations regress on a future model swap or prompt
// edit, this evaluator's pass rate drops in DD LLMObs and we notice.
//
// The rule list is intentionally conservative — only high-confidence
// patterns where a single tool is unambiguously correct. Ambiguous
// queries (e.g. "Houston flood risk" — could fan out to FEMA + bridges
// + water) return N/A so we don't flag false positives.
//
// Adding a new rule: keep the regex narrow (test against your seed pool +
// production traces to avoid over-matching), and pick a tool that should
// ALWAYS be in the call list — not "might also have been called".
public class ToolRoutingAccuracyEvaluator : IResponseEvaluator
{
    public string Label => "tool_routing_accuracy";

    // Each rule: regex over the user's query (case-insensitive) → tool
    // name that MUST appear in the response's tools_called list.
    private static readonly (Regex Pattern, string ExpectedTool, string RuleName)[] Rules =
    {
        // Bridges — NBI is the only bridge tool we have
        (new Regex(@"\bbridge(s)?\b.*\b(rating|condition|deficient|scour|sufficiency|NBI|inspection)\b",
            RegexOptions.IgnoreCase | RegexOptions.Compiled),
            "get_bridge_condition", "bridge_condition_query"),

        // Water SDWA violations — query_type=violations path
        (new Regex(@"\b(SDWA|violation|drinking water|PWSID|water system)\b",
            RegexOptions.IgnoreCase | RegexOptions.Compiled),
            "get_water_infrastructure", "water_compliance_query"),

        // TWDB Texas water plan
        (new Regex(@"\b(TWDB|state water plan|water plan project|desalination)\b",
            RegexOptions.IgnoreCase | RegexOptions.Compiled),
            "get_water_infrastructure", "twdb_water_plan_query"),

        // ERCOT — Texas-only grid storage
        (new Regex(@"\b(ERCOT|energy storage|grid stress|ESR\b|battery storage)\b",
            RegexOptions.IgnoreCase | RegexOptions.Compiled),
            "get_ercot_energy_storage", "ercot_query"),

        // Disaster history — FEMA only
        (new Regex(@"\b(FEMA|disaster declaration|hurricane|flood declaration|tornado|wildfire history)\b",
            RegexOptions.IgnoreCase | RegexOptions.Compiled),
            "get_disaster_history", "disaster_history_query"),

        // Document drafting — must chain through knowledge first
        (new Regex(@"\b(draft|scope of work|SOW\b|risk summary|cost estimate|funding memo)\b",
            RegexOptions.IgnoreCase | RegexOptions.Compiled),
            "search_project_knowledge", "document_chain_pre"),

        // Federal procurement — historical awards keyword
        (new Regex(@"\b(USASpending|contract award|won contracts|past contracts|historical awards|incumbent)\b",
            RegexOptions.IgnoreCase | RegexOptions.Compiled),
            "get_contract_awards", "contract_awards_query"),

        // Federal procurement — active opportunities keyword
        (new Regex(@"\b(SAM\.gov|RFP|solicitation|active opportunit|open contract|federal grant)\b",
            RegexOptions.IgnoreCase | RegexOptions.Compiled),
            "get_procurement_opportunities", "procurement_opportunities_query"),

        // TxDOT specifics
        (new Regex(@"\b(TxDOT|AADT|annual average daily traffic|Texas highway construction)\b",
            RegexOptions.IgnoreCase | RegexOptions.Compiled),
            "search_txdot_open_data", "txdot_query"),
    };

    public Task<EvalResult> EvaluateAsync(EvalInput input, CancellationToken ct)
    {
        var matchedRules = Rules
            .Where(r => r.Pattern.IsMatch(input.Query))
            .ToList();

        if (matchedRules.Count == 0)
            return Task.FromResult(new EvalResult("boolean", true,
                "Rule N/A: no high-confidence routing pattern matched the query"));

        var calledTools = new HashSet<string>(input.ToolsCalled, StringComparer.Ordinal);
        var failed = matchedRules
            .Where(r => !calledTools.Contains(r.ExpectedTool))
            .ToList();

        if (failed.Count == 0)
            return Task.FromResult(new EvalResult("boolean", true,
                $"Routed correctly: {string.Join(", ", matchedRules.Select(r => $"{r.RuleName} → {r.ExpectedTool}"))}"));

        var details = string.Join("; ", failed.Select(r =>
            $"{r.RuleName} expected {r.ExpectedTool} but tools called were [{string.Join(", ", input.ToolsCalled)}]"));
        return Task.FromResult(new EvalResult("boolean", false, $"Routing mismatch: {details}"));
    }
}
