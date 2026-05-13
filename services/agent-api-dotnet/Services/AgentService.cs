using System.Diagnostics;
using System.Diagnostics.Metrics;
using System.Text.Json;
using Microsoft.Agents.AI;
using Microsoft.Extensions.AI;
using InfraAdvisor.AgentApi.Models;

namespace InfraAdvisor.AgentApi.Services;

// Agent orchestrator backed by Microsoft Agents Framework.
//
// Replaces ~500 lines of hand-rolled router→specialist→tool-loop code with
// the MAF builder pipeline. The single ChatClientAgent has access to every
// MCP tool exposed by mcp-server-dotnet; the model picks which to call.
// MAF's .UseOpenTelemetry() emits the invoke_agent span; M.E.AI's
// .UseOpenTelemetry() on the chat client (set up in Program.cs) emits the
// chat + execute_tool spans inside it.
//
// Session memory + persistence is handled by AgentSessionStore (Redis JSON
// round-trip via SerializeSessionAsync / DeserializeSessionAsync), not here.
public class AgentService
{
    private readonly AIAgent _agent;
    private readonly AgentSessionStore _sessions;
    private readonly RetrievalService _retrieval;
    private readonly Histogram<double> _faithfulnessHistogram;
    private readonly ILogger<AgentService> _logger;

    // ActivitySource for manual spans that the M.E.AI / MAF decorators don't
    // emit on their own — task (classify_domain) here, retrieval inside
    // RetrievalService. Same source name TelemetrySetup AddSource's so they
    // get exported.
    private static readonly ActivitySource ActivitySource =
        new(Observability.TelemetrySetup.ActivitySourceName);

    public AgentService(
        AIAgent agent,
        AgentSessionStore sessions,
        RetrievalService retrieval,
        IMeterFactory meterFactory,
        ILogger<AgentService> logger)
    {
        _agent = agent;
        _sessions = sessions;
        _retrieval = retrieval;
        _logger = logger;

        var meter = meterFactory.Create(Observability.TelemetrySetup.ActivitySourceName);
        _faithfulnessHistogram = meter.CreateHistogram<double>(
            "agent.faithfulness_score",
            description: "Faithfulness evaluation score for agent responses");
    }

    public async Task<AgentResult> RunAgentAsync(
        string query,
        string sessionId,
        string deployment,
        CancellationToken ct = default)
    {
        // 1. Task: classify the query domain (manual span — pure CS, no LLM).
        var domain = ClassifyDomainTraced(query);

        // 2. Retrieval: vector-search the best-practices corpus. Emits a
        //    retrieval span which wraps a framework-emitted embedding span
        //    (query embedding). Failures degrade silently — the agent still
        //    answers without retrieved context.
        var retrieved = await _retrieval.RetrieveAsync(query, topK: 3, ct);

        // 3. Inject retrieval context as a system-style preamble. Cheap and
        //    keeps the agent prompt unchanged structurally.
        var augmentedQuery = retrieved.Count > 0
            ? $"Relevant InfraAdvisor best-practice context:\n{string.Join("\n\n", retrieved)}\n\n---\n\nUser question: {query}"
            : query;

        // Session lookup / restore / save round-trip wraps the MAF agent call.
        var session = await _sessions.GetOrCreateAsync(_agent, sessionId, ct);

        AgentResponse response;
        try
        {
            response = await _agent.RunAsync(augmentedQuery, session, cancellationToken: ct);
        }
        catch (Exception ex)
        {
            _logger.LogWarning("agent.RunAsync failed for session={SessionId}: {Error}",
                sessionId, ex.Message);
            throw;
        }

        await _sessions.SaveAsync(_agent, sessionId, session, ct);

        var answer = response.Text ?? "";
        var sources = ExtractSourcesFromResponse(response);
        var toolsCalled = ExtractToolsCalledFromResponse(response);

        return new AgentResult(
            Answer: answer,
            Sources: sources,
            ToolsCalled: toolsCalled,
            QueryDomain: domain);
    }

    // Wraps ClassifyDomain in a manual Activity tagged so DD LLMObs renders
    // it as a "task" kind span (alongside the agent / chat / tool / embedding
    // / retrieval kinds emitted elsewhere in this trace).
    private static string ClassifyDomainTraced(string query)
    {
        using var activity = ActivitySource.StartActivity("classify_domain", ActivityKind.Internal);
        activity?.SetTag("gen_ai.operation.name", "classify_domain");
        activity?.SetTag("dd.llmobs.span.kind", "task");
        activity?.SetTag("input.value", query);

        var domain = ClassifyDomain(query);

        activity?.SetTag("output.value", domain);
        activity?.SetTag("query.domain", domain);
        return domain;
    }

    public void RecordFaithfulness(double score, string sessionId, string domain)
    {
        score = Math.Clamp(score, 0.0, 1.0);
        _faithfulnessHistogram.Record(score,
            new KeyValuePair<string, object?>("session.id", sessionId),
            new KeyValuePair<string, object?>("query.domain", domain));
    }

    // ── Extract source citations from the most recent agent response ─────────
    // MCP tool results are nested JSON; each item often carries a "_source"
    // field. Walk the assistant + tool messages and collect distinct _source
    // values for the AgentResult.Sources list that the UI renders.
    private static List<string> ExtractSourcesFromResponse(AgentResponse response)
    {
        var sources = new List<string>();
        foreach (var message in response.Messages)
        {
            foreach (var content in message.Contents)
            {
                if (content is FunctionResultContent fr && fr.Result is not null)
                {
                    TryExtractSources(fr.Result.ToString() ?? "", sources);
                }
                else if (content is TextContent tc)
                {
                    TryExtractSources(tc.Text, sources);
                }
            }
        }
        return sources;
    }

    private static List<string> ExtractToolsCalledFromResponse(AgentResponse response)
    {
        var seen = new HashSet<string>();
        var result = new List<string>();
        foreach (var message in response.Messages)
        {
            foreach (var content in message.Contents)
            {
                if (content is FunctionCallContent fc && seen.Add(fc.Name))
                    result.Add(fc.Name);
            }
        }
        return result;
    }

    private static void TryExtractSources(string maybeJson, List<string> sources)
    {
        if (string.IsNullOrWhiteSpace(maybeJson)) return;
        try
        {
            using var doc = JsonDocument.Parse(maybeJson);
            WalkForSource(doc.RootElement, sources);
        }
        catch { /* not JSON — nothing to extract */ }
    }

    private static void WalkForSource(JsonElement el, List<string> sources)
    {
        if (el.ValueKind == JsonValueKind.Object)
        {
            if (el.TryGetProperty("_source", out var src) && src.ValueKind == JsonValueKind.String)
            {
                var s = src.GetString();
                if (!string.IsNullOrEmpty(s) && !sources.Contains(s)) sources.Add(s);
            }
            foreach (var prop in el.EnumerateObject())
                WalkForSource(prop.Value, sources);
        }
        else if (el.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in el.EnumerateArray())
                WalkForSource(item, sources);
        }
    }

    // Lightweight keyword-based domain classifier — same logic as before,
    // kept for the AgentResult.QueryDomain field that downstream eval +
    // suggestion code reads.
    public static string ClassifyDomain(string query)
    {
        var q = query.ToLowerInvariant();
        foreach (var (domain, keywords) in DomainKeywords)
            if (keywords.Any(k => q.Contains(k))) return domain;
        return "general";
    }

    private static readonly Dictionary<string, List<string>> DomainKeywords = new()
    {
        ["engineering"]          = new() { "bridge", "highway", "rail", "nbi", "aadt", "sufficiency", "txdot", "traffic", "structural", "civil", "assessment", "inspection" },
        ["water"]                = new() { "water", "sdwis", "twdb", "pwsid", "violation", "desalination", "aquifer", "wastewater", "mep" },
        ["energy"]               = new() { "energy", "eia", "grid", "generation", "fuel", "solar", "wind", "ercot", "storage", "esr", "utility" },
        ["construction"]         = new() { "construction", "project delivery", "schedule", "commissioning", "site" },
        ["operations"]           = new() { "operations", "maintenance", "asset management", "facilities", "o&m", "lifecycle" },
        ["document"]             = new() { "draft", "scope of work", "sow", "risk summary", "cost estimate", "funding", "basis of design", "report", "memo" },
        ["business_development"] = new() { "rfp", "solicitation", "contract award", "procurement", "bid", "grant", "sam.gov", "usaspending", "competitive", "proposal", "opportunity" },
    };
}
