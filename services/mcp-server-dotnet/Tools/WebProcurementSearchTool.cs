using System.ComponentModel;
using System.Text.Json;
using System.Text.RegularExpressions;
using Azure;
using Azure.AI.OpenAI;
using ModelContextProtocol.Server;
using OpenAI.Chat;

namespace InfraAdvisor.McpServer.Tools;

[McpServerToolType]
public sealed class WebProcurementSearchTool(IHttpClientFactory httpFactory, ILogger<WebProcurementSearchTool> logger)
{
    private const string TavilySearchUrl = "https://api.tavily.com/search";

    private static readonly List<string> IncludeDomains = new()
    {
        ".gov", ".us", "demandstar.com", "bidnetdirect.com", "bonfirehub.com"
    };

    private static readonly Dictionary<string, string> SectorTerms = new()
    {
        ["transportation"] = "transportation infrastructure",
        ["water"] = "water treatment infrastructure",
        ["energy"] = "energy power infrastructure",
        ["buildings"] = "commercial building construction",
        ["environmental"] = "environmental remediation",
    };

    [McpServerTool(Name = "search_web_procurement")]
    [Description(
        "Search government websites for state/local RFPs, bond elections, and budget announcements. " +
        "Uses Tavily Search to find .gov and .us procurement pages, then extracts structured data using gpt-4.1-nano. " +
        "Requires TAVILY_API_KEY env var. " +
        "sector: 'transportation' | 'water' | 'energy' | 'buildings' | 'environmental'. " +
        "result_type: 'rfp' | 'bond' | 'budget' | 'award' | 'any'.")]
    public async Task<string> SearchWebProcurementAsync(
        [Description("Search query")] string query,
        [Description("Geography filter, e.g. 'Texas' or 'Harris County TX'")] string? geography = null,
        [Description("Infrastructure sector: 'transportation', 'water', 'energy', 'buildings', or 'environmental'")] string? sector = null,
        [Description("Result type: 'rfp', 'bond', 'budget', 'award', or 'any'")] string? result_type = null,
        [Description("Maximum number of results to return")] int limit = 8,
        CancellationToken cancellationToken = default)
    {
        var tavilyKey = Environment.GetEnvironmentVariable("TAVILY_API_KEY") ?? "";
        if (string.IsNullOrEmpty(tavilyKey))
            return SerializeError("TAVILY_API_KEY not configured", "web_procurement", false);

        // Build search query
        var searchQuery = BuildSearchQuery(query, geography, sector, result_type);
        logger.LogInformation("web_procurement search query={Query} limit={Limit}", searchQuery, limit);

        // Call Tavily
        var searchResult = await TavilySearchAsync(tavilyKey, searchQuery, limit, cancellationToken);
        if (searchResult is null)
            return SerializeError("Tavily search returned null", "tavily", true);
        if (searchResult.ContainsKey("error"))
            return JsonSerializer.Serialize(searchResult);

        var items = searchResult.TryGetValue("items", out var i) && i is List<Dictionary<string, string>> list ? list : new List<Dictionary<string, string>>();

        if (items.Count == 0)
        {
            logger.LogInformation("web_procurement: no results returned from Tavily");
            return JsonSerializer.Serialize(Array.Empty<object>());
        }

        // Extract structured data from each result concurrently (run in thread pool to avoid blocking)
        var extractionTasks = items.Select(item =>
            Task.Run(() => ExtractProcurementData(item.GetValueOrDefault("content", ""), item.GetValueOrDefault("url", "")), cancellationToken)
        ).ToList();

        var extractionResults = await Task.WhenAll(extractionTasks);
        var results = extractionResults.Where(r => r != null).ToList();

        logger.LogInformation("web_procurement: {Count} results from {Total} Tavily results", results.Count, items.Count);
        return JsonSerializer.Serialize(results);
    }

    private async Task<Dictionary<string, object?>?> TavilySearchAsync(string apiKey, string query, int limit, CancellationToken cancellationToken)
    {
        var body = new
        {
            api_key = apiKey,
            query,
            search_depth = "advanced",
            include_domains = IncludeDomains,
            max_results = limit,
        };

        var client = httpFactory.CreateClient();
        client.Timeout = TimeSpan.FromSeconds(30);

        HttpResponseMessage resp;
        try
        {
            var content = new StringContent(JsonSerializer.Serialize(body), System.Text.Encoding.UTF8, "application/json");
            resp = await client.PostAsync(TavilySearchUrl, content, cancellationToken);
        }
        catch (TaskCanceledException)
        {
            return new Dictionary<string, object?> { ["error"] = "Tavily Search API request timed out.", ["retriable"] = true };
        }
        catch (Exception ex)
        {
            return new Dictionary<string, object?> { ["error"] = $"Tavily Search API request failed: {ex.Message}", ["retriable"] = true };
        }

        var statusCode = (int)resp.StatusCode;
        if (statusCode >= 400)
            return new Dictionary<string, object?> { ["error"] = $"Tavily Search API error: HTTP {statusCode}", ["retriable"] = statusCode >= 500 };

        var json = await resp.Content.ReadAsStringAsync(cancellationToken);
        using var doc = JsonDocument.Parse(json);
        var data = doc.RootElement;

        var rawResults = data.TryGetProperty("results", out var r) ? r.EnumerateArray().ToList() : new List<JsonElement>();
        var items = rawResults.Select(item => new Dictionary<string, string>
        {
            ["url"] = item.TryGetProperty("url", out var u) ? u.GetString() ?? "" : "",
            ["title"] = item.TryGetProperty("title", out var t) ? t.GetString() ?? "" : "",
            ["content"] = item.TryGetProperty("content", out var c) ? c.GetString() ?? "" : "",
        }).ToList();

        return new Dictionary<string, object?> { ["items"] = items };
    }

    private Dictionary<string, object?>? ExtractProcurementData(string text, string sourceUrl)
    {
        try
        {
            var azEndpoint = Environment.GetEnvironmentVariable("AZURE_OPENAI_ENDPOINT") ?? "";
            var azApiKey = Environment.GetEnvironmentVariable("AZURE_OPENAI_API_KEY") ?? "";
            if (string.IsNullOrEmpty(azEndpoint) || string.IsNullOrEmpty(azApiKey))
            {
                logger.LogDebug("Azure OpenAI not configured for web procurement extraction");
                return null;
            }

            var deployment = Environment.GetEnvironmentVariable("AZURE_OPENAI_EVAL_DEPLOYMENT_NAME") ?? "gpt-4.1-nano";
            var aoaiClient = new AzureOpenAIClient(new Uri(azEndpoint), new AzureKeyCredential(azApiKey));
            var chatClient = aoaiClient.GetChatClient(deployment);

            var maxText = text.Length > 2500 ? text[..2500] : text;
            var prompt = $"""
                Extract procurement information from this government webpage text.
                Return ONLY valid JSON with these fields:
                - agency_name (string or null)
                - project_title (string or null)
                - project_description (max 200 chars, string or null)
                - estimated_value_usd (integer or null)
                - deadline (ISO date string or null)
                - contact_email (string or null)
                - source_url (string - use "{sourceUrl}")
                - result_type ("rfp" | "award" | "bond" | "budget" | "other")
                - confidence ("high" | "medium" | "low")

                Return null for any field you cannot confidently determine.
                Text: {maxText}
                """;

            var response = chatClient.CompleteChat(new[]
            {
                new UserChatMessage(prompt)
            }, new ChatCompletionOptions { Temperature = 0, MaxOutputTokenCount = 400 });

            var content = response.Value.Content[0].Text ?? "";
            var jsonMatch = Regex.Match(content, @"\{.*\}", RegexOptions.Singleline);
            if (!jsonMatch.Success) return null;

            using var doc = JsonDocument.Parse(jsonMatch.Value);
            var data = doc.RootElement;

            var confidence = data.TryGetProperty("confidence", out var conf) ? conf.GetString() : null;
            var resultType = data.TryGetProperty("result_type", out var rt) ? rt.GetString() : null;

            if (confidence == "low" || resultType == "other") return null;

            // Build result dict
            var result = new Dictionary<string, object?>();
            foreach (var prop in data.EnumerateObject())
            {
                result[prop.Name] = prop.Value.ValueKind switch
                {
                    JsonValueKind.String => prop.Value.GetString(),
                    JsonValueKind.Number => prop.Value.TryGetInt64(out var i) ? (object)i : prop.Value.GetDouble(),
                    JsonValueKind.True => true,
                    JsonValueKind.False => false,
                    JsonValueKind.Null => null,
                    _ => prop.Value.ToString(),
                };
            }
            result["_source"] = "web_search";
            result["_search_engine"] = "Tavily";
            return result;
        }
        catch (Exception ex)
        {
            logger.LogDebug("Extraction failed for {Url}: {Error}", sourceUrl, ex.Message);
            return null;
        }
    }

    private static string BuildSearchQuery(string query, string? geography, string? sector, string? resultType)
    {
        var parts = new List<string> { query };

        if (!string.IsNullOrEmpty(geography))
            parts.Add("site:.gov OR site:.us");

        if (!string.IsNullOrEmpty(sector) && SectorTerms.TryGetValue(sector, out var sectorTerm))
            parts.Add(sectorTerm);

        if (resultType == "rfp")
            parts.Add("\"request for proposals\" OR \"RFP\" OR \"solicitation\"");
        else if (resultType == "bond")
            parts.Add("\"bond election\" OR \"municipal bond\"");
        else if (resultType == "budget")
            parts.Add("\"infrastructure budget\" OR \"capital improvement plan\"");

        return string.Join(" ", parts);
    }

    private static string SerializeError(string message, string source, bool retriable) =>
        JsonSerializer.Serialize(new { error = message, source, retriable });
}
