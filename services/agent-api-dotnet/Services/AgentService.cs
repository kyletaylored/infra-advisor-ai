using System.Diagnostics;
using System.Diagnostics.Metrics;
using System.Text.Json;
using Azure;
using Azure.AI.OpenAI;
using InfraAdvisor.AgentApi.Models;
using OpenAI.Chat;
using InfraAdvisor.AgentApi.Observability;

namespace InfraAdvisor.AgentApi.Services;

public class AgentService
{
    private readonly McpClientService _mcpClient;
    private readonly MemoryService _memory;
    private readonly ActivitySource _activitySource;
    private readonly Histogram<double> _faithfulnessHistogram;
    private readonly AzureOpenAIClient _azureClient;
    private readonly string _defaultDeployment;
    private readonly ILogger<AgentService> _logger;

    // ── Tool partitions ───────────────────────────────────────────────────────
    private static readonly Dictionary<string, List<string>?> ToolPartitions = new()
    {
        ["engineering"] = new List<string>
        {
            "get_bridge_condition",
            "get_disaster_history",
            "search_txdot_open_data",
            "get_water_infrastructure",
            "get_energy_infrastructure",
            "get_ercot_energy_storage",
            "search_project_knowledge",
            "draft_document",
        },
        ["water_energy"] = new List<string>
        {
            "get_water_infrastructure",
            "get_energy_infrastructure",
            "get_ercot_energy_storage",
            "get_disaster_history",
            "search_project_knowledge",
            "draft_document",
        },
        ["business_development"] = new List<string>
        {
            "get_procurement_opportunities",
            "get_contract_awards",
            "search_web_procurement",
            "search_project_knowledge",
        },
        ["document"] = new List<string>
        {
            "draft_document",
            "search_project_knowledge",
            "get_bridge_condition",
            "get_water_infrastructure",
            "get_energy_infrastructure",
        },
        ["general"] = null, // all tools
    };

    // ── Domain keywords ───────────────────────────────────────────────────────
    private static readonly Dictionary<string, List<string>> DomainKeywords = new()
    {
        ["engineering"] = new List<string> { "bridge", "highway", "rail", "nbi", "aadt", "sufficiency", "txdot", "traffic", "structural", "civil", "assessment", "inspection" },
        ["water"] = new List<string> { "water", "sdwis", "twdb", "pwsid", "violation", "desalination", "aquifer", "wastewater", "mep" },
        ["energy"] = new List<string> { "energy", "eia", "grid", "generation", "fuel", "solar", "wind", "ercot", "storage", "esr", "utility" },
        ["construction"] = new List<string> { "construction", "project delivery", "schedule", "commissioning", "site" },
        ["operations"] = new List<string> { "operations", "maintenance", "asset management", "facilities", "o&m", "lifecycle" },
        ["document"] = new List<string> { "draft", "scope of work", "sow", "risk summary", "cost estimate", "funding", "basis of design", "report", "memo" },
        ["business_development"] = new List<string> { "rfp", "solicitation", "contract award", "procurement", "bid", "grant", "sam.gov", "usaspending", "competitive", "proposal", "opportunity" },
    };

    // ── Router system prompt ──────────────────────────────────────────────────
    private const string RouterSystemPrompt =
        "You are a routing assistant for an infrastructure consulting AI system. " +
        "Given a user query, select the most appropriate specialist agent to handle it. " +
        "The firm serves Architecture, Engineering, Construction, Operations, and Management (AECOM) practice areas.\n\n" +
        "- engineering: civil/structural infrastructure data — bridges (NBI), transportation (TxDOT), water systems " +
        "(SDWIS/TWDB), energy (EIA/ERCOT), disaster impacts, structural assessments, resilience analysis\n" +
        "- water_energy: focused water/MEP/environmental queries — SDWIS compliance, TWDB supply plans, EIA generation data, ERCOT grid storage\n" +
        "- business_development: AEC procurement intelligence — SAM.gov opportunities, grants.gov, USASpending.gov awards, state/local RFPs, competitive analysis\n" +
        "- document: deliverable drafting — SOWs, basis-of-design reports, risk summaries, cost estimates, funding memos, O&M plans\n" +
        "- general: multi-domain, unclear scope, or queries spanning more than two AECOM practice areas\n\n" +
        "Provide a brief handoff_context (1-2 sentences) summarizing the key focus for the specialist.\n\n" +
        "Respond with valid JSON only: {\"specialist\": \"...\", \"handoff_context\": \"...\"}";

    // ── Specialist system prompts ─────────────────────────────────────────────
    private static readonly Dictionary<string, string> SpecialistSystemPrompts = new()
    {
        ["engineering"] =
            "You are InfraAdvisor Engineering Specialist, an expert in civil, structural, " +
            "and environmental infrastructure analysis supporting Architecture, Engineering, Construction, " +
            "Operations, and Management (AECOM) practice areas at a global consulting firm.\n\n" +
            "Your focus: bridge condition and structural deficiency (FHWA NBI), transportation data (TxDOT AADT), " +
            "water system compliance and supply planning (EPA SDWIS, TWDB), energy generation and grid data " +
            "(EIA, ERCOT), disaster risk impacts on infrastructure, and engineering document drafting.\n\n" +
            "Guidelines:\n" +
            "1. Always cite source IDs: NBI structure numbers, PWSID, TWDB project IDs, EIA plant IDs, TxDOT dataset IDs, FEMA declaration IDs\n" +
            "2. Sort assets by descending risk: bridges by ascending sufficiency rating; water systems by descending violation count\n" +
            "3. Flag critical conditions explicitly: scour vulnerability, fracture-critical status, load rating deficiencies, open SDWA violations, grid stress periods\n" +
            "4. For multi-domain engineering queries, combine asset data with search_project_knowledge for firm precedents\n" +
            "5. For document drafts, call search_project_knowledge first for relevant templates and prior project context\n" +
            "6. Do not speculate about conditions not in the data — say \"not available in the dataset\"\n" +
            "7. Keep factual lookups concise; provide detailed context for design or document deliverables",

        ["water_energy"] =
            "You are InfraAdvisor Water & Energy Specialist, an expert in water systems " +
            "and energy infrastructure analysis supporting MEP engineering and environmental practice areas " +
            "at a global Architecture, Engineering, Construction, Operations, and Management (AECOM) firm.\n\n" +
            "Your focus: public water system compliance and supply planning (EPA SDWIS, TWDB 2026 State Water Plan), " +
            "EIA electricity generation and capacity data, ERCOT Texas grid energy storage resources (ESR), " +
            "and environmental/utility engineering deliverables.\n\n" +
            "Guidelines:\n" +
            "1. Always cite PWSID, TWDB project IDs, EIA state/fuel identifiers, or ERCOT ESR resource IDs\n" +
            "2. Sort water systems by descending violation count (most violations = highest risk first)\n" +
            "3. Flag open Safe Drinking Water Act violations, boil-water notices, and unresolved enforcement actions\n" +
            "4. For water queries, combine get_water_infrastructure (compliance) with search_project_knowledge (firm history)\n" +
            "5. For ERCOT queries, note grid stress periods, peak demand windows, and storage discharge patterns\n" +
            "6. For document drafts, call search_project_knowledge first for relevant templates\n" +
            "7. Do not speculate about conditions not in the data — say \"not available in the dataset\"\n" +
            "8. Keep factual lookups concise; detailed for engineering design documents and environmental reports",

        ["business_development"] =
            "You are InfraAdvisor Business Development Specialist, an expert in " +
            "federal procurement intelligence and market positioning supporting the Management practice area " +
            "at a global Architecture, Engineering, Construction, Operations, and Management (AECOM) firm.\n\n" +
            "Your focus: federal contract awards for AEC services (USASpending.gov), active federal opportunities " +
            "(SAM.gov, grants.gov), state/local RFPs and bond elections (web procurement search), and competitive " +
            "landscape analysis for infrastructure and environmental programs.\n\n" +
            "Guidelines:\n" +
            "1. Always call get_contract_awards BEFORE get_procurement_opportunities — understanding who won " +
            "similar work informs positioning for open opportunities\n" +
            "2. Cite USASpending award IDs, SAM.gov solicitation numbers, grants.gov opportunity IDs\n" +
            "3. For web procurement results, always note the confidence field and flag medium-confidence " +
            "extractions explicitly so users can verify before acting\n" +
            "4. Identify incumbent contractors, pricing benchmarks, and agency spending patterns from awards data\n" +
            "5. Match NAICS codes to AEC domains: 237110 (water/wastewater), 237310 (highway/road), " +
            "237990 (other heavy civil), 541330 (engineering services), 541310 (architecture services)\n" +
            "6. Flag grant deadlines and application windows prominently\n" +
            "7. Keep competitive intelligence summaries actionable — focus on win themes and differentiators\n" +
            "8. NEVER ask the user to specify a date range for SAM.gov or USASpending queries — the tools " +
            "always default to the last 12 months automatically. If the tool returns a date-range error, " +
            "report that SAM.gov data is temporarily unavailable rather than asking the user for dates",

        ["document"] =
            "You are InfraAdvisor Advisory Specialist, an expert in drafting consulting " +
            "deliverables across Architecture, Engineering, Construction, Operations, and Management (AECOM) " +
            "practice areas for a global infrastructure consulting firm.\n\n" +
            "Your focus: Scopes of Work (SOW), basis-of-design reports, risk summaries, cost estimates, " +
            "funding memos, technical reports, and operations & maintenance plans across civil/structural, " +
            "MEP, environmental, and program management domains.\n\n" +
            "Guidelines:\n" +
            "1. Always call search_project_knowledge FIRST to retrieve relevant templates and prior project context\n" +
            "2. Structure documents with clear sections: executive summary, scope, methodology, deliverables, timeline\n" +
            "3. For risk summaries, query asset condition data to ground the document in actual findings\n" +
            "4. Cite data sources for factual sections (NBI structure numbers, PWSID, EIA IDs, FEMA declaration IDs)\n" +
            "5. Flag where client-specific placeholders need to be filled in before delivery\n" +
            "6. Keep cost estimates clearly marked as order-of-magnitude unless detailed scope supports more precision\n" +
            "7. Match document tone to audience: technical for engineering peer review, executive for leadership",

        ["general"] =
            "You are InfraAdvisor, a technical AI assistant for consultants across " +
            "Architecture, Engineering, Construction, Operations, and Management (AECOM) practice areas " +
            "at a global infrastructure consulting firm.\n\n" +
            "Your expertise spans the full AEC/O/M project lifecycle: feasibility and planning, " +
            "civil and structural engineering (bridges, highways, rail), MEP and environmental systems " +
            "(water, wastewater, energy), construction project delivery, asset operations and maintenance, " +
            "and management advisory (program management, BD, risk, compliance).\n\n" +
            "You have access to tools covering bridges (FHWA NBI), disasters (FEMA), energy (EIA/ERCOT), " +
            "water systems (EPA SDWIS/TWDB), Texas transportation (TxDOT), firm knowledge base, " +
            "document drafting, and federal procurement intelligence (SAM.gov, USASpending.gov, Tavily).\n\n" +
            "Guidelines:\n" +
            "1. Always cite the data source for factual claims\n" +
            "2. Sort assets by descending risk: bridges by ascending sufficiency rating; water systems by descending violation count\n" +
            "3. Flag material risks explicitly — scour vulnerability, load rating deficiencies, repeat flood events, SDWA violations\n" +
            "4. For water and environmental queries, combine get_water_infrastructure with search_project_knowledge\n" +
            "5. For draft documents or deliverables, call search_project_knowledge first\n" +
            "6. Do not speculate about asset conditions not in the data\n" +
            "7. Respond in the same language the user writes in\n" +
            "8. Keep responses concise for data lookups; detailed for engineering analysis and document drafts\n" +
            "9. For business development queries, always call get_contract_awards before get_procurement_opportunities\n" +
            "10. When search_web_procurement returns results, flag medium-confidence extractions explicitly\n" +
            "11. NEVER ask the user for a date range — procurement tools default to the last 12 months automatically",
    };

    public AgentService(
        McpClientService mcpClient,
        MemoryService memory,
        ActivitySource activitySource,
        IMeterFactory meterFactory,
        IConfiguration configuration,
        ILogger<AgentService> logger)
    {
        _mcpClient = mcpClient;
        _memory = memory;
        _activitySource = activitySource;
        _logger = logger;

        var meter = meterFactory.Create(Observability.TelemetrySetup.ActivitySourceName);
        _faithfulnessHistogram = meter.CreateHistogram<double>(
            "agent.faithfulness_score",
            description: "Faithfulness evaluation score for agent responses");

        var endpoint = configuration["AZURE_OPENAI_ENDPOINT"]
            ?? Environment.GetEnvironmentVariable("AZURE_OPENAI_ENDPOINT")
            ?? throw new InvalidOperationException("AZURE_OPENAI_ENDPOINT is required");
        var apiKey = configuration["AZURE_OPENAI_API_KEY"]
            ?? Environment.GetEnvironmentVariable("AZURE_OPENAI_API_KEY")
            ?? throw new InvalidOperationException("AZURE_OPENAI_API_KEY is required");
        _defaultDeployment = configuration["AZURE_OPENAI_DEPLOYMENT"]
            ?? Environment.GetEnvironmentVariable("AZURE_OPENAI_DEPLOYMENT")
            ?? "gpt-4.1-mini";

        _azureClient = new AzureOpenAIClient(new Uri(endpoint), new AzureKeyCredential(apiKey));
    }

    public async Task<AgentResult> RunAgentAsync(
        string query,
        string sessionId,
        string deployment,
        string? rumSessionId = null,
        CancellationToken ct = default)
    {
        var obsSessionId = rumSessionId ?? sessionId;
        var queryDomain = ClassifyDomain(query);
        var dep = string.IsNullOrWhiteSpace(deployment) ? _defaultDeployment : deployment;
        var chatClient = _azureClient.GetChatClient(dep);

        // Load session history
        var history = await _memory.LoadHistoryAsync(sessionId);

        // ── Router ────────────────────────────────────────────────────────────
        string specialist;
        string handoffContext;

        using (var routerActivity = _activitySource.StartActivity("router"))
        {
            routerActivity?.SetTag("query.domain", queryDomain);
            routerActivity?.SetTag("session.id", obsSessionId);

            (specialist, handoffContext) = await RunRouterAsync(query, chatClient, ct);

            routerActivity?.SetTag("router.specialist", specialist);
            routerActivity?.SetTag("router.handoff_context", handoffContext.Length > 200
                ? handoffContext[..200] : handoffContext);
        }

        // ── Fetch tools + apply partition ─────────────────────────────────────
        var allMcpTools = await _mcpClient.ListToolsAsync(ct);
        List<McpToolDefinition> specialistTools;
        if (ToolPartitions.TryGetValue(specialist, out var allowed) && allowed != null)
            specialistTools = allMcpTools.Where(t => allowed.Contains(t.Name)).ToList();
        else
            specialistTools = allMcpTools;

        // ── Build system prompt + chat tools ──────────────────────────────────
        var sysPrompt = SpecialistSystemPrompts.TryGetValue(specialist, out var sp)
            ? sp : SpecialistSystemPrompts["general"];

        if (!string.IsNullOrWhiteSpace(handoffContext) && handoffContext != query)
            sysPrompt = $"{sysPrompt}\n\n[Routing context]: {handoffContext}";

        var chatTools = specialistTools
            .Select(t => ChatTool.CreateFunctionTool(
                functionName: t.Name,
                functionDescription: t.Description,
                functionParameters: t.InputSchema.ValueKind != JsonValueKind.Undefined
                    ? BinaryData.FromObjectAsJson(t.InputSchema)
                    : BinaryData.FromString("{\"type\":\"object\",\"properties\":{}}")))
            .ToList();

        // Build message list: system + history + current query
        var messages = new List<ChatMessage>();
        messages.Add(new SystemChatMessage(sysPrompt));
        foreach (var h in history)
        {
            if (h.Role == "human")
                messages.Add(new UserChatMessage(h.Content));
            else if (h.Role == "ai")
                messages.Add(new AssistantChatMessage(h.Content));
        }
        messages.Add(new UserChatMessage(query));

        // ── ReAct loop ────────────────────────────────────────────────────────
        string answer = "";
        var toolsCalled = new List<string>();
        var contextChunks = new List<string>();
        var sources = new List<string>();
        const int MaxIterations = 10;

        using (var specialistActivity = _activitySource.StartActivity($"specialist-{specialist}"))
        {
            specialistActivity?.SetTag("specialist", specialist);
            specialistActivity?.SetTag("specialist.tools_available", specialistTools.Count.ToString());
            specialistActivity?.SetTag("query.domain", queryDomain);
            specialistActivity?.SetTag("session.id", obsSessionId);

            var options = new ChatCompletionOptions();
            foreach (var tool in chatTools)
                options.Tools.Add(tool);

            for (int i = 0; i < MaxIterations; i++)
            {
                var iterSw = Stopwatch.StartNew();
                var lastUserMsg = messages.OfType<UserChatMessage>().LastOrDefault();
                var promptText = lastUserMsg?.Content?.FirstOrDefault()?.Text ?? query;
                using var llmSpan = LlmTelemetry.StartLlmActivity(
                    modelName: dep,
                    prompt: promptText.Length > 200 ? promptText[..200] : promptText,
                    taskType: $"specialist_{specialist}_iter{i}",
                    provider: "azure");
                llmSpan?.SetTag("llm.span_type", "specialist");
                llmSpan?.SetTag("specialist", specialist);
                llmSpan?.SetTag("iteration", i);

                var response = await chatClient.CompleteChatAsync(messages, options, ct);
                iterSw.Stop();
                var completion = response.Value;

                if (completion.FinishReason == ChatFinishReason.ToolCalls)
                {
                    LlmTelemetry.EndLlmActivity(llmSpan, "tool_calls", true, iterSw.ElapsedMilliseconds);
                    messages.Add(new AssistantChatMessage(completion));

                    foreach (var toolCall in completion.ToolCalls)
                    {
                        var toolName = toolCall.FunctionName;
                        if (!toolsCalled.Contains(toolName))
                            toolsCalled.Add(toolName);

                        string toolResult;
                        try
                        {
                            using var argDoc = JsonDocument.Parse(toolCall.FunctionArguments);
                            toolResult = await _mcpClient.InvokeToolAsync(toolName, argDoc.RootElement.Clone(), ct);
                        }
                        catch (Exception ex)
                        {
                            _logger.LogWarning("Tool {ToolName} failed: {Error}", toolName, ex.Message);
                            toolResult = $"{{\"error\": \"{ex.Message}\"}}";
                        }

                        contextChunks.Add(toolResult.Length > 500 ? toolResult[..500] : toolResult);
                        ExtractSources(toolResult, sources);
                        messages.Add(new ToolChatMessage(toolCall.Id, toolResult));
                    }
                }
                else if (completion.FinishReason == ChatFinishReason.Stop)
                {
                    answer = completion.Content.Count > 0 ? completion.Content[0].Text : "";
                    LlmTelemetry.EndLlmActivity(llmSpan, answer.Length > 500 ? answer[..500] : answer, true, iterSw.ElapsedMilliseconds);
                    break;
                }
                else
                {
                    _logger.LogWarning("ReAct loop ended with finish reason: {Reason}", completion.FinishReason);
                    LlmTelemetry.EndLlmActivity(llmSpan, completion.FinishReason.ToString(), false, iterSw.ElapsedMilliseconds);
                    break;
                }
            }

            specialistActivity?.SetTag("tools_called", string.Join(",", toolsCalled));
            specialistActivity?.SetTag("sources.count", sources.Count.ToString());
        }

        // ── Faithfulness eval (fire-and-forget) ───────────────────────────────
        var capturedQuery = query;
        var capturedAnswer = answer;
        var capturedContext = string.Join("\n\n", contextChunks);
        var capturedSessionId = obsSessionId;
        var capturedDomain = queryDomain;
        var evalDeployment = dep;

        _ = Task.Run(async () =>
        {
            try
            {
                var evalClient = _azureClient.GetChatClient(evalDeployment);
                var evalMessages = new List<ChatMessage>
                {
                    new SystemChatMessage(
                        "You are a faithfulness evaluator. Given a context and a question-answer pair, " +
                        "rate how faithful the answer is to the context on a scale from 0.0 to 1.0. " +
                        "Return ONLY a single decimal number between 0.0 and 1.0, nothing else."),
                    new UserChatMessage(
                        $"Context: {capturedContext}\nQuestion: {capturedQuery}\nAnswer: {capturedAnswer}"),
                };
                var evalOptions = new ChatCompletionOptions { MaxOutputTokenCount = 5 };
                var evalResponse = await evalClient.CompleteChatAsync(evalMessages, evalOptions);
                var scoreText = evalResponse.Value.Content.Count > 0
                    ? evalResponse.Value.Content[0].Text.Trim() : "";
                if (double.TryParse(scoreText, System.Globalization.NumberStyles.Float,
                    System.Globalization.CultureInfo.InvariantCulture, out var score))
                {
                    score = Math.Clamp(score, 0.0, 1.0);
                    _faithfulnessHistogram.Record(score,
                        new KeyValuePair<string, object?>("session.id", capturedSessionId),
                        new KeyValuePair<string, object?>("query.domain", capturedDomain));
                }
            }
            catch
            {
                // non-fatal
            }
        });

        return new AgentResult(answer, sources, toolsCalled, queryDomain);
    }

    // ── Router ────────────────────────────────────────────────────────────────

    private async Task<(string specialist, string handoffContext)> RunRouterAsync(
        string query, ChatClient chatClient, CancellationToken ct)
    {
        try
        {
            var routerMessages = new List<ChatMessage>
            {
                new SystemChatMessage(RouterSystemPrompt),
                new UserChatMessage(query),
            };
            var routerOptions = new ChatCompletionOptions
            {
                ResponseFormat = ChatResponseFormat.CreateJsonObjectFormat(),
            };

            var sw = Stopwatch.StartNew();
            using var llmActivity = LlmTelemetry.StartLlmActivity(
                modelName: _defaultDeployment,
                prompt: query,
                taskType: "router",
                provider: "azure");
            llmActivity?.SetTag("llm.span_type", "router");

            var response = await chatClient.CompleteChatAsync(routerMessages, routerOptions, ct);
            sw.Stop();
            var text = response.Value.Content.Count > 0 ? response.Value.Content[0].Text : "{}";
            using var doc = JsonDocument.Parse(text);
            var root = doc.RootElement;
            var specialist = root.TryGetProperty("specialist", out var s)
                ? s.GetString() ?? "general" : "general";
            var handoffContext = root.TryGetProperty("handoff_context", out var h)
                ? h.GetString() ?? query : query;

            if (!ToolPartitions.ContainsKey(specialist))
                specialist = "general";

            LlmTelemetry.EndLlmActivity(
                activity: llmActivity,
                response: specialist,
                isSuccess: true,
                latencyMs: sw.ElapsedMilliseconds);

            return (specialist, handoffContext);
        }
        catch (Exception ex)
        {
            _logger.LogWarning("Router failed (non-fatal): {Error}", ex.Message);
            return ("general", query);
        }
    }

    // ── Domain classifier ─────────────────────────────────────────────────────

    public static string ClassifyDomain(string query)
    {
        var q = query.ToLowerInvariant();
        foreach (var (domain, keywords) in DomainKeywords)
        {
            if (keywords.Any(kw => q.Contains(kw)))
                return domain;
        }
        return "general";
    }

    // ── Source extractor ──────────────────────────────────────────────────────

    private static void ExtractSources(string toolResult, List<string> sources)
    {
        try
        {
            using var doc = JsonDocument.Parse(toolResult);
            var root = doc.RootElement;

            // Determine items to inspect
            IEnumerable<JsonElement> items;
            if (root.ValueKind == JsonValueKind.Array)
                items = root.EnumerateArray();
            else
                items = new[] { root };

            foreach (var item in items)
            {
                if (item.ValueKind != JsonValueKind.Object) continue;

                // Pattern 1: {type:"text", text:"{nested JSON with _source}"}
                if (item.TryGetProperty("type", out var typeEl) &&
                    typeEl.GetString() == "text" &&
                    item.TryGetProperty("text", out var textEl))
                {
                    var nested = textEl.GetString() ?? "";
                    try
                    {
                        using var innerDoc = JsonDocument.Parse(nested);
                        var innerRoot = innerDoc.RootElement;
                        IEnumerable<JsonElement> innerItems = innerRoot.ValueKind == JsonValueKind.Array
                            ? innerRoot.EnumerateArray()
                            : new[] { innerRoot };
                        foreach (var record in innerItems)
                        {
                            if (record.ValueKind == JsonValueKind.Object &&
                                record.TryGetProperty("_source", out var srcEl))
                            {
                                var src = srcEl.GetString();
                                if (!string.IsNullOrEmpty(src) && !sources.Contains(src))
                                    sources.Add(src);
                            }
                        }
                    }
                    catch { /* not nested JSON */ }
                }
                else
                {
                    // Pattern 2: direct {_source: "..."}
                    if (item.TryGetProperty("_source", out var srcEl2))
                    {
                        var src = srcEl2.GetString();
                        if (!string.IsNullOrEmpty(src) && !sources.Contains(src))
                            sources.Add(src);
                    }
                }
            }
        }
        catch { /* not JSON */ }
    }

    // Public accessor for the Azure client (used by SuggestionService)
    internal AzureOpenAIClient AzureClient => _azureClient;
    internal string DefaultDeployment => _defaultDeployment;
}
