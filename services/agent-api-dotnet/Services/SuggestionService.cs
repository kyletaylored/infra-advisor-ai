using System.Text.Json;
using System.Text.RegularExpressions;
using Azure.AI.OpenAI;
using InfraAdvisor.AgentApi.Models;
using StackExchange.Redis;
using OpenAI.Chat;

namespace InfraAdvisor.AgentApi.Services;

public class SuggestionService
{
    private readonly IConnectionMultiplexer _redis;
    private readonly AzureOpenAIClient _azure;
    private readonly string _deployment;
    private readonly ILogger<SuggestionService> _logger;

    private const string PoolKey = "infra-advisor:suggestions:pool";
    private const int PoolMax = 80;
    private const int PoolMin = 20;

    private static readonly string AllTools =
        "get_bridge_condition (FHWA National Bridge Inventory — structural ratings, ADT, sufficiency), " +
        "get_disaster_history (FEMA disaster declarations and hazard mitigation grants), " +
        "get_energy_infrastructure (EIA electricity generation and capacity by state/fuel), " +
        "get_water_infrastructure (EPA SDWIS water system compliance and TWDB water plans), " +
        "get_ercot_energy_storage (ERCOT Texas grid energy storage resource 4-second charging data), " +
        "search_txdot_open_data (TxDOT Open Data portal — AADT traffic counts, construction projects, highway datasets), " +
        "search_project_knowledge (firm knowledge base — case studies, risk frameworks, templates), " +
        "draft_document (generate SOW, risk summary, cost estimate, or funding memo), " +
        "get_procurement_opportunities (SAM.gov and grants.gov — active federal contract opportunities and open grant programs), " +
        "get_contract_awards (USASpending.gov — historical federal contract awards for competitive intelligence and pricing benchmarks), " +
        "search_web_procurement (Brave Search — state and local RFPs, bond elections, and government budget announcements)";

    public static readonly List<SuggestionItem> FallbackSuggestions = new()
    {
        new SuggestionItem(
            "Deficient bridges",
            "List structurally deficient bridges in Texas with ADT over 10,000, sorted by sufficiency rating lowest first."),
        new SuggestionItem(
            "SDWA violations",
            "Which Texas community water systems have open Safe Drinking Water Act violations serving more than 10,000 people?"),
        new SuggestionItem(
            "Infrastructure opportunities",
            "What active federal procurement opportunities exist for infrastructure engineering services on SAM.gov with NAICS codes for civil or environmental work?"),
        new SuggestionItem(
            "Disaster risk counties",
            "Which Texas counties have received 5 or more FEMA disaster declarations since 2010, and what hazard types are most frequent?"),
    };

    private static readonly string SuggestionsPromptTemplate =
        "You are generating follow-up question suggestions for an AI assistant serving consultants at an " +
        "AEC/O&M (Architecture, Engineering, Construction / Operations & Maintenance) infrastructure firm.\n\n" +
        "The user just asked:\n{query}\n\n" +
        "The AI used these data tools: {sources}\n\n" +
        "The AI answered (truncated):\n{answer}\n\n" +
        "Available tools the user can query next:\n{tools}\n\n" +
        "Generate exactly 4 concise follow-up questions that are natural next steps given this conversation. " +
        "Each should explore a different AEC/O&M practice area angle — engineering risk, construction delivery, " +
        "operational resilience, management/BD, or document drafting. " +
        "Keep labels short (2-5 words, no emojis). Keep queries specific, data-grounded, and immediately actionable.\n\n" +
        "Return ONLY valid JSON, no markdown fences, no explanation:\n" +
        "{\"suggestions\": [{\"label\": \"...\", \"query\": \"...\"}, {\"label\": \"...\", \"query\": \"...\"}, {\"label\": \"...\", \"query\": \"...\"}, {\"label\": \"...\", \"query\": \"...\"}]}";

    // ── Tool catalog — keep IN SYNC with the MCP server [Description(...)] attrs.
    // Suggestion-pool prompts inject this string so the LLM generates questions
    // that map cleanly to a single tool call with realistic args, instead of
    // questions that no tool can actually answer.
    private const string ToolCatalog = """
        Available MCP tools and what they can answer (use this list to ground
        every suggestion in something the system can actually look up):

        1. get_bridge_condition — FHWA National Bridge Inventory. All US bridges over 20 ft. Fields: condition ratings (0-9), BRIDGE_CONDITION Good/Fair/Poor, scour-critical flag, ADT, year built, location. Input: 2-char FIPS state code (e.g. '48' = TX, '06' = CA). Works nationwide.

        2. get_disaster_history — OpenFEMA major-disaster + emergency declarations 1953-present. Nationwide. Filter by states (2-letter abbrev), incident_types (Flood, Hurricane, Tornado, etc.), date range.

        3. get_energy_infrastructure — EIA state-level annual electricity statistics. All 50 states. data_series: 'generation' | 'capacity' | 'fuel_mix'. Fuel codes: SUN, WND, NG, COL, NUC, HYC, BIO, GEO, PET.

        4. get_ercot_energy_storage — ERCOT public data API. TEXAS ONLY (~90% of TX, excludes El Paso and SPP regions). Battery storage 4-second charging data. Use for ERCOT-specific grid questions only.

        5. get_water_infrastructure — Dispatched by query_type:
           - 'water_systems' (EPA SDWIS) → all US public water systems inventory
           - 'violations' (EPA SDWIS) → SDWA violations nationwide
           - 'water_plan_projects' (TWDB) → TEXAS ONLY recommended water projects, regions A-P
           Always cite PWSID for individual systems.

        6. search_txdot_open_data — TxDOT Open Data portal (ArcGIS). TEXAS ONLY. AADT counts, construction projects, highway geometry. query_type: 'catalog_search' | 'traffic_counts' | 'construction_projects'.

        7. search_project_knowledge — Azure AI Search index of internal firm content (case studies, prior SOWs, templates, best practices). Call BEFORE draft_document.

        8. draft_document — Renders one of 4 Scriban templates: 'scope_of_work' | 'risk_summary' | 'cost_estimate_scaffold' | 'funding_positioning_memo'. Always call search_project_knowledge first.

        9. get_procurement_opportunities — SAM.gov + grants.gov. ACTIVE/OPEN federal solicitations and grants. NEVER asks for a date range. AEC NAICS: 237310 (highway), 237110 (water/sewer), 237990 (heavy civil), 541330 (engineering services).

        10. get_contract_awards — USASpending.gov. HISTORICAL federal contract awards. Default window: past 2 years. CALL THIS BEFORE get_procurement_opportunities for BD research — know past winners before pursuing open opportunities.

        11. search_web_procurement — Azure OpenAI web_search_preview. State/local RFPs, bond elections, budget announcements on .gov / .us / DemandStar / BidNet / BonfireHub. Use ONLY for non-federal procurement.

        Hard constraints:
        - For Texas-specific tools (ERCOT, TxDOT, TWDB), only use Texas in the question.
        - For NAICS codes, prefer ['237310', '237110', '237990', '541330'].
        - For federal BD questions, suggest the get_contract_awards → get_procurement_opportunities sequence.
        - For 'recent disaster' questions, name an incident_type and a state.
        - For energy questions outside Texas grid, use get_energy_infrastructure (not ERCOT).
        """;

    private static readonly string[] PoolBatchPrompts =
    {
        // Engineering: structural, civil, environmental
        ToolCatalog + "\n\n" +
        "Generate exactly 10 opening questions an infrastructure engineer at a consulting " +
        "firm would ask. Each question must map cleanly to ONE of the tools above with " +
        "REALISTIC arguments. Cover: bridge condition (get_bridge_condition with state " +
        "FIPS), water system violations (get_water_infrastructure violations), energy mix " +
        "(get_energy_infrastructure with state list), TxDOT AADT, FEMA disaster patterns.\n" +
        "Every question must name a specific state / county / threshold / time window. " +
        "Avoid 'how do I' or 'what is' phrasings — these are data-lookup questions. " +
        "No emojis in labels. Labels 2-5 words.\n" +
        "Return ONLY valid JSON, no markdown:\n" +
        "{\"suggestions\": [{\"label\": \"...\", \"query\": \"...\"}, ... 10 items ...]}",

        // Construction: procurement, delivery, project data
        ToolCatalog + "\n\n" +
        "Generate exactly 10 opening questions a construction project manager or BD " +
        "director would ask. Each question maps to get_contract_awards, get_procurement_" +
        "opportunities, or search_web_procurement. Many BD questions are best as the " +
        "PAIR get_contract_awards → get_procurement_opportunities — phrase the suggestion " +
        "so it naturally invokes both ('find historical TxDOT bridge awards and current " +
        "open opportunities under NAICS 237310').\n" +
        "Every question must reference a specific NAICS code, agency, dollar threshold, " +
        "or geography. No emojis in labels. Labels 2-5 words.\n" +
        "Return ONLY valid JSON, no markdown:\n" +
        "{\"suggestions\": [{\"label\": \"...\", \"query\": \"...\"}, ... 10 items ...]}",

        // Operations: resilience, risk, asset lifecycle
        ToolCatalog + "\n\n" +
        "Generate exactly 10 opening questions an asset manager or resilience planner " +
        "would ask. Map cleanly to: get_disaster_history (cite specific incident_types " +
        "and state list), get_bridge_condition (cite max_lowest_rating or structurally_" +
        "deficient_only=true), get_water_infrastructure violations, get_ercot_energy_" +
        "storage (Texas only).\n" +
        "Every question must reference a specific hazard type, county, time range, or " +
        "asset class. No emojis in labels. Labels 2-5 words.\n" +
        "Return ONLY valid JSON, no markdown:\n" +
        "{\"suggestions\": [{\"label\": \"...\", \"query\": \"...\"}, ... 10 items ...]}",

        // Management / Advisory: documents, BD, firm knowledge
        ToolCatalog + "\n\n" +
        "Generate exactly 10 opening questions a program manager or practice leader would " +
        "ask. Each one should require search_project_knowledge (to pull templates / " +
        "precedent) and may chain into draft_document. Document types available: " +
        "scope_of_work, risk_summary, cost_estimate_scaffold, funding_positioning_memo.\n" +
        "Every question must describe a concrete deliverable, project type, or decision " +
        "context. No emojis in labels. Labels 2-5 words.\n" +
        "Return ONLY valid JSON, no markdown:\n" +
        "{\"suggestions\": [{\"label\": \"...\", \"query\": \"...\"}, ... 10 items ...]}",
    };

    // Curated golden-path seed pool — hand-verified to work end-to-end against
    // the current tool set. Loaded into Redis on cold start (when the LLM-
    // generated pool is empty) so the user's first-touch experience is always
    // reliable. Each query is short, names the exact data domain, and has a
    // clear single-tool or sequenced-tool path through the agent.
    public static readonly IReadOnlyList<SuggestionItem> SeedPool = new[]
    {
        // Bridges — single tool, fast
        new SuggestionItem(
            "Worst Texas bridges",
            "List the 25 worst-rated bridges in Texas by lowest condition rating."),
        new SuggestionItem(
            "California scour bridges",
            "Find structurally deficient bridges in California with scour-critical flags set."),
        new SuggestionItem(
            "Harris County deficient",
            "Show structurally deficient bridges in Harris County, Texas with ADT over 10,000."),

        // Water — single tool, three datasets
        new SuggestionItem(
            "Texas SDWA violations",
            "Which Texas community water systems have open Safe Drinking Water Act violations serving more than 10,000 people?"),
        new SuggestionItem(
            "Texas desalination plans",
            "List recommended desalination projects from the TWDB 2026 State Water Plan."),

        // FEMA disasters — single tool
        new SuggestionItem(
            "Recent Texas hurricanes",
            "How many hurricane disaster declarations has Texas had in the last 10 years?"),

        // Energy — single tool
        new SuggestionItem(
            "Texas renewable mix",
            "What's the renewable energy generation share for Texas in the last 5 years?"),

        // TxDOT — single tool, Texas-only
        new SuggestionItem(
            "TxDOT pavement data",
            "Find TxDOT Open Data datasets related to pavement condition."),

        // Federal procurement — chained tools (the BD golden path)
        new SuggestionItem(
            "TX highway BD",
            "Find recent federal contract awards for highway construction in Texas under NAICS 237310, then list open opportunities matching the same NAICS."),
        new SuggestionItem(
            "Water engineering RFPs",
            "Show active federal solicitations for water engineering services (NAICS 541330 or 237110) with bid deadlines in the next 60 days."),

        // Document drafting — chained: knowledge → draft
        new SuggestionItem(
            "SOW for bridge rehab",
            "Pull templates and prior projects for bridge rehabilitation, then draft a scope_of_work for an IH-35 bridge corridor project."),

        // Cross-domain — exercises 2-3 tools
        new SuggestionItem(
            "Flood-risk bridge audit",
            "For Harris County, Texas: list structurally deficient bridges, recent flood declarations in the last 5 years, and water systems with violations."),
    };

    public SuggestionService(
        IConnectionMultiplexer redis,
        AzureOpenAIClient azure,
        IConfiguration configuration,
        ILogger<SuggestionService> logger)
    {
        _redis = redis;
        _azure = azure;
        _deployment = configuration["AZURE_OPENAI_DEPLOYMENT"]
            ?? Environment.GetEnvironmentVariable("AZURE_OPENAI_DEPLOYMENT")
            ?? "gpt-4.1-mini";
        _logger = logger;
    }

    private ChatClient ChatClient => _azure.GetChatClient(_deployment);

    public async Task<long> GetPoolSizeAsync()
    {
        try
        {
            var db = _redis.GetDatabase();
            return await db.ListLengthAsync(PoolKey);
        }
        catch
        {
            return 0;
        }
    }

    public async Task<List<SuggestionItem>> GetRandomFromPoolAsync(int n)
    {
        try
        {
            var db = _redis.GetDatabase();
            var all = await db.ListRangeAsync(PoolKey, 0, -1);
            if (all.Length < n) return new();

            var list = all.ToList();
            // Fisher-Yates shuffle
            var rng = Random.Shared;
            for (int i = list.Count - 1; i > 0; i--)
            {
                int j = rng.Next(i + 1);
                (list[i], list[j]) = (list[j], list[i]);
            }

            var result = new List<SuggestionItem>();
            foreach (var raw in list.Take(n))
            {
                try
                {
                    var item = JsonSerializer.Deserialize<SuggestionItem>(raw.ToString(),
                        new JsonSerializerOptions { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower });
                    if (item != null) result.Add(item);
                }
                catch { /* skip malformed items */ }
            }
            return result;
        }
        catch (Exception ex)
        {
            _logger.LogWarning("GetRandomFromPoolAsync failed: {Error}", ex.Message);
            return new();
        }
    }

    public async Task AddToPoolAsync(List<SuggestionItem> items)
    {
        if (items.Count == 0) return;
        try
        {
            var db = _redis.GetDatabase();
            var serialized = items
                .Select(i => (RedisValue)JsonSerializer.Serialize(new { label = i.Label, query = i.Query }))
                .ToArray();
            await db.ListRightPushAsync(PoolKey, serialized);
            var size = await db.ListLengthAsync(PoolKey);
            if (size > PoolMax)
                await db.ListTrimAsync(PoolKey, size - PoolMax, -1);
        }
        catch (Exception ex)
        {
            _logger.LogWarning("AddToPoolAsync failed: {Error}", ex.Message);
        }
    }

    public async Task FillPoolAsync()
    {
        // Cold-start seed: if the pool is empty (fresh Redis, first deploy),
        // populate the curated golden-path SeedPool first so the user's
        // first-touch experience surfaces hand-verified queries. The LLM
        // batch below then runs concurrently to add variety.
        try
        {
            var poolSize = await GetPoolSizeAsync();
            if (poolSize == 0)
            {
                await AddToPoolAsync(SeedPool.ToList());
                _logger.LogInformation("Suggestion pool seeded with {Count} curated golden-path queries", SeedPool.Count);
            }
        }
        catch (Exception ex)
        {
            _logger.LogWarning("Seed-pool initialization failed: {Error}", ex.Message);
        }

        var prompt = PoolBatchPrompts[Random.Shared.Next(PoolBatchPrompts.Length)];
        try
        {
            var messages = new List<ChatMessage> { new UserChatMessage(prompt) };
            var response = await ChatClient.CompleteChatAsync(messages);
            var content = response.Value.Content.Count > 0 ? response.Value.Content[0].Text : "";
            var items = ParseSuggestions(content);
            if (items.Count > 0)
            {
                await AddToPoolAsync(items);
                var poolSize = await GetPoolSizeAsync();
                _logger.LogInformation("Suggestion pool refilled: +{Count} items (pool={Size})", items.Count, poolSize);
            }
        }
        catch (Exception ex)
        {
            _logger.LogWarning("FillPoolAsync failed: {Error}", ex.Message);
        }
    }

    public async Task<List<SuggestionItem>> GetContextualSuggestionsAsync(
        string query, string answer, List<string> sources)
    {
        var sourcesStr = sources.Count > 0 ? string.Join(", ", sources) : "general knowledge";
        var prompt = SuggestionsPromptTemplate
            .Replace("{query}", query.Length > 500 ? query[..500] : query)
            .Replace("{sources}", sourcesStr)
            .Replace("{answer}", answer.Length > 800 ? answer[..800] : answer)
            .Replace("{tools}", AllTools);

        try
        {
            var messages = new List<ChatMessage> { new UserChatMessage(prompt) };
            var response = await ChatClient.CompleteChatAsync(messages);
            var content = response.Value.Content.Count > 0 ? response.Value.Content[0].Text : "";
            var parsed = ParseSuggestions(content);
            if (parsed.Count > 0) return parsed;
        }
        catch (Exception ex)
        {
            _logger.LogWarning("GetContextualSuggestionsAsync LLM call failed: {Error}", ex.Message);
        }

        return FallbackSuggestions;
    }

    public List<SuggestionItem> ParseSuggestions(string text)
    {
        // Primary: parse as JSON directly
        try
        {
            var data = JsonDocument.Parse(text.Trim());
            if (data.RootElement.TryGetProperty("suggestions", out var arr))
            {
                var items = new List<SuggestionItem>();
                foreach (var s in arr.EnumerateArray())
                {
                    if (s.TryGetProperty("label", out var label) &&
                        s.TryGetProperty("query", out var q))
                    {
                        items.Add(new SuggestionItem(label.GetString() ?? "", q.GetString() ?? ""));
                        if (items.Count == 4) break;
                    }
                }
                if (items.Count > 0) return items;
            }
        }
        catch { /* fall through to regex */ }

        // Fallback: regex extract first JSON object with "suggestions"
        var match = Regex.Match(text, @"\{.*""suggestions"".*\}", RegexOptions.Singleline);
        if (match.Success)
        {
            try
            {
                var data = JsonDocument.Parse(match.Value);
                if (data.RootElement.TryGetProperty("suggestions", out var arr))
                {
                    var items = new List<SuggestionItem>();
                    foreach (var s in arr.EnumerateArray())
                    {
                        if (s.TryGetProperty("label", out var label) &&
                            s.TryGetProperty("query", out var q))
                        {
                            items.Add(new SuggestionItem(label.GetString() ?? "", q.GetString() ?? ""));
                            if (items.Count == 4) break;
                        }
                    }
                    if (items.Count > 0) return items;
                }
            }
            catch { /* give up */ }
        }

        return new();
    }
}
