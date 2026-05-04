using System.Text.Json;
using System.Text.RegularExpressions;
using Azure.AI.OpenAI;
using InfraAdvisor.AgentApi.Models;
using OpenAI.Chat;
using StackExchange.Redis;

namespace InfraAdvisor.AgentApi.Services;

public class SuggestionService
{
    private readonly IConnectionMultiplexer _redis;
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
        "Architecture, Engineering, Construction, Operations, and Management (AECOM) firm.\n\n" +
        "The user just asked:\n{query}\n\n" +
        "The AI used these data tools: {sources}\n\n" +
        "The AI answered (truncated):\n{answer}\n\n" +
        "Available tools the user can query next:\n{tools}\n\n" +
        "Generate exactly 4 concise follow-up questions that are natural next steps given this conversation. " +
        "Each should explore a different AECOM practice area angle — engineering risk, construction delivery, " +
        "operational resilience, management/BD, or document drafting. " +
        "Keep labels short (2-5 words, no emojis). Keep queries specific, data-grounded, and immediately actionable.\n\n" +
        "Return ONLY valid JSON, no markdown fences, no explanation:\n" +
        "{\"suggestions\": [{\"label\": \"...\", \"query\": \"...\"}, {\"label\": \"...\", \"query\": \"...\"}, {\"label\": \"...\", \"query\": \"...\"}, {\"label\": \"...\", \"query\": \"...\"}]}";

    private static readonly string[] PoolBatchPrompts =
    {
        // Engineering: structural, civil, environmental
        "Generate exactly 10 specific opening questions an infrastructure engineer at an AECOM-style consulting " +
        "firm would ask an AI assistant backed by FHWA NBI, EPA SDWIS, EIA, ERCOT, TxDOT, and FEMA data.\n" +
        "Focus on: structural condition rankings, sufficiency ratings, scour risk, water system violations, " +
        "energy grid capacity by fuel type, traffic volume thresholds, and cross-hazard exposure.\n" +
        "Every question must cite a specific threshold, data field, geography, or time window. No generic questions. " +
        "No emojis in labels. Labels 2-5 words.\n" +
        "Return ONLY valid JSON, no markdown:\n" +
        "{\"suggestions\": [{\"label\": \"...\", \"query\": \"...\"}, ... 10 items ...]}",

        // Construction: procurement, delivery, project data
        "Generate exactly 10 specific opening questions a construction project manager or BD director at an " +
        "AECOM-style consulting firm would ask an AI assistant backed by SAM.gov, USASpending.gov, and state " +
        "procurement portals.\n" +
        "Focus on: active federal solicitations, contract award benchmarks by NAICS code, incumbent contractor " +
        "analysis, grant program deadlines, bond election schedules, and price-per-unit benchmarks.\n" +
        "Every question must reference a specific NAICS code, agency, dollar threshold, or geography. No emojis in labels. Labels 2-5 words.\n" +
        "Return ONLY valid JSON, no markdown:\n" +
        "{\"suggestions\": [{\"label\": \"...\", \"query\": \"...\"}, ... 10 items ...]}",

        // Operations: resilience, risk, asset lifecycle
        "Generate exactly 10 specific opening questions an asset manager or resilience planner at an AECOM-style " +
        "consulting firm would ask an AI assistant backed by FEMA OpenFEMA, FHWA NBI, EPA SDWIS, and EIA data.\n" +
        "Focus on: repeat disaster declarations by county and hazard type, flood and scour risk to bridge assets, " +
        "water system outage history, grid stress events, multi-hazard exposure scoring, and infrastructure age profiles.\n" +
        "Every question must reference a specific hazard type, county, time range, or asset class. No emojis in labels. Labels 2-5 words.\n" +
        "Return ONLY valid JSON, no markdown:\n" +
        "{\"suggestions\": [{\"label\": \"...\", \"query\": \"...\"}, ... 10 items ...]}",

        // Management/Advisory: documents, BD, firm knowledge
        "Generate exactly 10 specific opening questions a program manager or practice leader at an AECOM-style " +
        "consulting firm would ask an AI assistant with access to a firm knowledge base, document drafting tools, " +
        "and procurement intelligence.\n" +
        "Focus on: SOW scaffolds for specific project types, risk framework selection, funding memo positioning, " +
        "order-of-magnitude cost estimates, competitive intelligence summaries, and similar prior project retrieval.\n" +
        "Every question must describe a concrete deliverable, project type, or decision context. No emojis in labels. Labels 2-5 words.\n" +
        "Return ONLY valid JSON, no markdown:\n" +
        "{\"suggestions\": [{\"label\": \"...\", \"query\": \"...\"}, ... 10 items ...]}",
    };

    public SuggestionService(IConnectionMultiplexer redis, ILogger<SuggestionService> logger)
    {
        _redis = redis;
        _logger = logger;
    }

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

    public async Task FillPoolAsync(ChatClient chatClient)
    {
        var prompt = PoolBatchPrompts[Random.Shared.Next(PoolBatchPrompts.Length)];
        try
        {
            var messages = new List<ChatMessage> { new UserChatMessage(prompt) };
            var response = await chatClient.CompleteChatAsync(messages);
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
        string query, string answer, List<string> sources, ChatClient chatClient)
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
            var response = await chatClient.CompleteChatAsync(messages);
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
