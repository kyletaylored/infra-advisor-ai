using System.ComponentModel;
using System.Text.Json;
using ModelContextProtocol.Server;

namespace InfraAdvisor.McpServer.Tools;

[McpServerToolType]
public sealed class TxDotOpenDataTool(IHttpClientFactory httpFactory, ILogger<TxDotOpenDataTool> logger)
{
    private static string TxDotHubUrl =>
        Environment.GetEnvironmentVariable("TXDOT_HUB_URL")
        ?? "https://gis-txdot.opendata.arcgis.com/api/search";

    private static readonly Dictionary<string, string> PresetQueries = new()
    {
        ["catalog_search"] = "",
        ["traffic_counts"] = "AADT annual average daily traffic volume county",
        ["construction_projects"] = "highway construction maintenance project lettings TxDOT",
    };

    [McpServerTool(Name = "search_txdot_open_data")]
    [Description(
        "Search the TxDOT Open Data portal (ArcGIS Hub) for Texas transportation datasets. " +
        "query_type must be exactly one of: " +
        "'catalog_search' (free-text search across all TxDOT datasets, requires query), " +
        "'traffic_counts' (Annual Average Daily Traffic AADT count datasets), " +
        "'construction_projects' (TxDOT highway construction and maintenance project datasets). " +
        "county: optional Texas county name to narrow results (e.g. 'Harris', 'Travis'). " +
        "Texas-specific — all data is from the TxDOT Open Data portal.")]
    public async Task<string> SearchTxDotOpenDataAsync(
        [Description("Query type: 'catalog_search', 'traffic_counts', or 'construction_projects'")] string query_type = "catalog_search",
        [Description("Free-text search query (required for catalog_search)")] string query = "",
        [Description("Optional Texas county name to narrow results")] string? county = null,
        [Description("Maximum number of results to return")] int limit = 20,
        [Description("Page number (1-based)")] int page = 1,
        CancellationToken cancellationToken = default)
    {
        var q = BuildSearchQuery(query_type, query, county);
        var paramPairs = new List<(string, string)>
        {
            ("q", q),
            ("collection", "Dataset"),
            ("num", Math.Min(limit, 50).ToString()),
            ("start", ((page - 1) * limit + 1).ToString()),
            ("sortBy", "relevance"),
        };

        var qs = string.Join("&", paramPairs.Select(p => $"{Uri.EscapeDataString(p.Item1)}={Uri.EscapeDataString(p.Item2)}"));
        var url = $"{TxDotHubUrl}?{qs}";

        var client = httpFactory.CreateClient();
        client.Timeout = TimeSpan.FromSeconds(20);

        HttpResponseMessage resp;
        try
        {
            resp = await client.GetAsync(url, cancellationToken);
        }
        catch (TaskCanceledException)
        {
            return SerializeError("TxDOT Hub API request timed out.", "txdot", true);
        }
        catch (Exception ex)
        {
            return SerializeError($"TxDOT Hub API request failed: {ex.Message}", "txdot", true);
        }

        var statusCode = (int)resp.StatusCode;
        if (statusCode >= 400)
            return SerializeError($"TxDOT Hub API error: HTTP {statusCode}", "txdot", statusCode >= 500);

        var json = await resp.Content.ReadAsStringAsync(cancellationToken);
        using var doc = JsonDocument.Parse(json);
        var body = doc.RootElement;

        List<JsonElement> items;
        if (body.ValueKind == JsonValueKind.Array)
            items = body.EnumerateArray().ToList();
        else if (body.TryGetProperty("results", out var r))
            items = r.EnumerateArray().ToList();
        else if (body.TryGetProperty("data", out var d))
            items = d.EnumerateArray().ToList();
        else if (body.TryGetProperty("items", out var i))
            items = i.EnumerateArray().ToList();
        else
            items = new List<JsonElement>();

        if (items.Count == 0)
        {
            logger.LogInformation("TxDOT Hub returned zero results for q={Q}", q);
            return JsonSerializer.Serialize(Array.Empty<object>());
        }

        var results = items.Select(NormaliseItem).ToList();
        logger.LogInformation("TxDOT Hub returned {Count} results for q={Q}", results.Count, q);
        return JsonSerializer.Serialize(results);
    }

    private static string BuildSearchQuery(string queryType, string query, string? county)
    {
        var parts = new List<string>();
        var preset = PresetQueries.GetValueOrDefault(queryType, "");
        if (!string.IsNullOrEmpty(preset)) parts.Add(preset);
        if (!string.IsNullOrEmpty(query)) parts.Add(query);
        if (!string.IsNullOrEmpty(county)) parts.Add($"{county} county");

        var result = string.Join(" ", parts).Trim();
        return string.IsNullOrEmpty(result) ? "Texas infrastructure" : result;
    }

    private static Dictionary<string, object?> NormaliseItem(JsonElement item)
    {
        // Hub v2 wraps in "attributes"
        var attrs = item.TryGetProperty("attributes", out var a) ? a : item;

        var url = GetStr(attrs, "url")
            ?? GetStr(attrs, "landingPage")
            ?? (attrs.TryGetProperty("source", out var src) && src.TryGetProperty("url", out var su) ? su.GetString() : null)
            ?? "";

        return new Dictionary<string, object?>
        {
            ["id"] = GetStr(attrs, "id") ?? GetStr(attrs, "itemId") ?? "",
            ["title"] = GetStr(attrs, "title") ?? GetStr(attrs, "name") ?? "",
            ["description"] = TruncateDescription(GetStr(attrs, "description") ?? GetStr(attrs, "snippet") ?? "", 400),
            ["type"] = GetStr(attrs, "type") ?? GetStr(attrs, "itemType") ?? "",
            ["url"] = url,
            ["tags"] = attrs.TryGetProperty("tags", out var tags) && tags.ValueKind == JsonValueKind.Array
                ? (object)tags.EnumerateArray().Select(t => t.ToString()).ToList()
                : new List<string>(),
            ["access"] = GetStr(attrs, "access") ?? "public",
            ["_source"] = "TxDOT_Open_Data",
            ["_retrieved_at"] = DateTime.UtcNow.ToString("o"),
        };
    }

    private static string TruncateDescription(string desc, int maxLen) =>
        desc.Length > maxLen ? desc[..maxLen] : desc;

    private static string? GetStr(JsonElement elem, string key)
    {
        if (!elem.TryGetProperty(key, out var val)) return null;
        return val.ValueKind == JsonValueKind.Null ? null : val.ToString();
    }

    private static string SerializeError(string message, string source, bool retriable) =>
        JsonSerializer.Serialize(new { error = message, source, retriable });
}
