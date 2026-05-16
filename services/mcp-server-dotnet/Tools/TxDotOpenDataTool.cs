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
        "TxDOT Open Data portal (ArcGIS Hub) — Texas transportation datasets. " +
        "_source: 'TxDOT'. No API key required.\n" +
        "Coverage: Texas only. Datasets include AADT (annual average daily traffic) " +
        "counts, active / completed highway construction projects, highway geometry, " +
        "district-level GIS layers.\n" +
        "Use when the user asks: AADT for a specific Texas highway / road; active or " +
        "planned TxDOT construction projects; traffic-count history; TxDOT GIS dataset " +
        "discovery; Texas roadway data.\n" +
        "Do NOT use for: federal highway data outside Texas (use get_bridge_condition " +
        "for NBI bridge data or local-state DOT portals); non-roadway Texas data " +
        "(TxDOT only — DPS, RRC, TWDB are separate); real-time traffic.\n" +
        "query_type semantics:\n" +
        "  'catalog_search' (default) → free-text search across the entire TxDOT " +
        "catalog; requires `query`\n" +
        "  'traffic_counts' → AADT-specific datasets (ignore `query`)\n" +
        "  'construction_projects' → project-tracker datasets (ignore `query`)")]
    public async Task<string> SearchTxDotOpenDataAsync(
        [Description("'catalog_search' (default; needs query) | 'traffic_counts' (AADT) | 'construction_projects'.")] string query_type = "catalog_search",
        [Description("Free-text search query — REQUIRED for catalog_search. Examples: 'pavement condition', 'pedestrian crashes', 'bridge inspection'.")] string query = "",
        [Description("Texas county name to narrow results. Examples: 'Harris', 'Travis', 'Bexar', 'Dallas', 'Tarrant'.")] string? county = null,
        [Description("Max results (1-50). Default 20.")] int limit = 20,
        [Description("Page (1-based).")] int page = 1,
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
