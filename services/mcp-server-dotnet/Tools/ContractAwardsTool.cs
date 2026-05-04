using System.ComponentModel;
using System.Net.Http.Json;
using System.Text.Json;
using ModelContextProtocol.Server;

namespace InfraAdvisor.McpServer.Tools;

[McpServerToolType]
public sealed class ContractAwardsTool(IHttpClientFactory httpFactory, ILogger<ContractAwardsTool> logger)
{
    private const string UsaSpendingUrl = "https://api.usaspending.gov/api/v2/search/spending_by_award/";

    private static readonly Dictionary<string, List<string>> NaicsMap = new()
    {
        ["water"] = new() { "237110" },
        ["sewer"] = new() { "237110" },
        ["bridge"] = new() { "237310" },
        ["highway"] = new() { "237310" },
        ["road"] = new() { "237310" },
        ["transportation"] = new() { "237310" },
        ["power"] = new() { "237130" },
        ["energy"] = new() { "237130" },
        ["pipeline"] = new() { "237120" },
        ["building"] = new() { "236220" },
        ["environmental"] = new() { "562910" },
        ["dam"] = new() { "237990" },
        ["flood"] = new() { "237990" },
    };

    private static readonly Dictionary<string, string> AwardTypeLabels = new()
    {
        ["A"] = "BPA Call",
        ["B"] = "Purchase Order",
        ["C"] = "Delivery Order",
        ["D"] = "Definitive Contract",
    };

    [McpServerTool(Name = "get_contract_awards")]
    [Description(
        "Search USASpending.gov for historical federal contract awards. " +
        "Returns competitive intelligence: who won similar work, at what price, and for which agencies. " +
        "Each result tagged _source: 'USASpending.gov'. No API key required. " +
        "date_from / date_to: ISO date strings (default: past 2 years through today).")]
    public async Task<string> GetContractAwardsAsync(
        [Description("Search query")] string query,
        [Description("State abbreviation or city, e.g. 'TX' or 'Austin TX'")] string? geography = null,
        [Description("NAICS codes to filter by")] List<string>? naics_codes = null,
        [Description("Agency names to filter by (case-insensitive substring match)")] List<string>? agency_names = null,
        [Description("Start date (ISO format, default: 2 years ago)")] string? date_from = null,
        [Description("End date (ISO format, default: today)")] string? date_to = null,
        [Description("Minimum award amount in USD")] int? min_award_usd = null,
        [Description("Maximum number of results to return")] int limit = 25,
        CancellationToken cancellationToken = default)
    {
        var today = DateTime.UtcNow.Date;
        var resolvedDateFrom = date_from ?? today.AddDays(-730).ToString("yyyy-MM-dd");
        var resolvedDateTo = date_to ?? today.ToString("yyyy-MM-dd");

        var keywords = query.Split(' ', StringSplitOptions.RemoveEmptyEntries)
            .Where(w => w.Length > 3)
            .ToList();
        if (keywords.Count == 0) keywords.Add(query);

        var naicsCodes = naics_codes ?? DeriveNaics(query);

        var filters = new Dictionary<string, object>
        {
            ["keywords"] = keywords,
            ["time_period"] = new[] { new { start_date = resolvedDateFrom, end_date = resolvedDateTo } },
            ["award_type_codes"] = new[] { "A", "B", "C", "D" },
            ["naics_codes"] = naicsCodes,
            ["place_of_performance_locations"] = BuildPlaceFilter(geography),
        };

        var payload = new
        {
            filters,
            fields = new[]
            {
                "Award ID", "Recipient Name", "recipient_id", "Award Amount", "Total Outlays",
                "Description", "Start Date", "End Date", "Awarding Agency", "Awarding Sub Agency",
                "Contract Award Type", "Place of Performance State Code", "Place of Performance City Name",
                "naics_code", "naics_description",
            },
            page = 1,
            limit,
            sort = "Award Amount",
            order = "desc",
        };

        var client = httpFactory.CreateClient();
        client.Timeout = TimeSpan.FromSeconds(30);

        HttpResponseMessage resp;
        try
        {
            resp = await client.PostAsJsonAsync(UsaSpendingUrl, payload, cancellationToken);
        }
        catch (TaskCanceledException)
        {
            return SerializeError("USASpending API request timed out.", "usaspending", true);
        }
        catch (Exception ex)
        {
            logger.LogError(ex, "Unexpected error in get_contract_awards");
            return SerializeError("Unexpected error querying USASpending.gov", "usaspending", false);
        }

        var statusCode = (int)resp.StatusCode;
        if (statusCode >= 400)
            return SerializeError($"USASpending API error: HTTP {statusCode}", "usaspending", statusCode >= 500);

        var json = await resp.Content.ReadAsStringAsync(cancellationToken);
        using var doc = JsonDocument.Parse(json);
        var body = doc.RootElement;

        var rawResults = body.TryGetProperty("results", out var r)
            ? r.EnumerateArray().ToList()
            : new List<JsonElement>();

        if (rawResults.Count == 0)
        {
            logger.LogInformation("USASpending returned zero results for query={Query}", query);
            return JsonSerializer.Serialize(Array.Empty<object>());
        }

        var awards = rawResults.Select(NormalizeAward).ToList();

        if (min_award_usd.HasValue)
            awards = awards.Where(a => a.TryGetValue("award_amount_usd", out var v) && v is double d && d >= min_award_usd.Value).ToList();

        if (agency_names?.Count > 0)
        {
            var filters2 = agency_names.Select(n => n.ToLowerInvariant()).ToList();
            awards = awards.Where(a =>
                filters2.Any(f =>
                    (a.TryGetValue("awarding_agency", out var ag) ? ag?.ToString() ?? "" : "").ToLowerInvariant().Contains(f) ||
                    (a.TryGetValue("awarding_sub_agency", out var sag) ? sag?.ToString() ?? "" : "").ToLowerInvariant().Contains(f)
                )).ToList();
        }

        logger.LogInformation("USASpending returned {Count} awards for query={Query}", awards.Count, query);
        return JsonSerializer.Serialize(awards);
    }

    private static Dictionary<string, object?> NormalizeAward(JsonElement result)
    {
        var awardId = GetStr(result, "Award ID") ?? "";
        return new Dictionary<string, object?>
        {
            ["award_id"] = awardId,
            ["recipient_name"] = GetStr(result, "Recipient Name") ?? "",
            ["award_amount_usd"] = GetDouble(result, "Award Amount") ?? GetDouble(result, "Total Outlays"),
            ["awarding_agency"] = GetStr(result, "Awarding Agency") ?? "",
            ["awarding_sub_agency"] = GetStr(result, "Awarding Sub Agency") ?? "",
            ["description"] = GetStr(result, "Description") ?? "",
            ["place_of_performance"] = $"{GetStr(result, "Place of Performance City Name") ?? ""} {GetStr(result, "Place of Performance State Code") ?? ""}".Trim(),
            ["start_date"] = GetStr(result, "Start Date") ?? "",
            ["end_date"] = GetStr(result, "End Date") ?? "",
            ["naics_description"] = GetStr(result, "naics_description") ?? "",
            ["contract_type"] = AwardTypeLabels.TryGetValue(GetStr(result, "Contract Award Type") ?? "", out var label)
                ? label
                : GetStr(result, "Contract Award Type") ?? "",
            ["usaspending_permalink"] = !string.IsNullOrEmpty(awardId)
                ? $"https://www.usaspending.gov/award/{awardId}"
                : "",
            ["_source"] = "USASpending.gov",
        };
    }

    private static object BuildPlaceFilter(string? geography)
    {
        if (string.IsNullOrEmpty(geography)) return Array.Empty<object>();
        var state = ExtractState(geography);
        if (state != null)
            return new[] { new { country = "USA", state } };
        return Array.Empty<object>();
    }

    private static string? ExtractState(string geography)
    {
        var g = geography.Trim();
        if (g.Length == 2 && char.IsLetter(g[0]) && char.IsLetter(g[1]))
            return g.ToUpperInvariant();
        foreach (var token in g.Split(' '))
        {
            if (token.Length == 2 && char.IsLetter(token[0]) && char.IsLetter(token[1]))
                return token.ToUpperInvariant();
        }
        return null;
    }

    private static List<string> DeriveNaics(string query)
    {
        var q = query.ToLowerInvariant();
        var codes = new List<string>();
        var seen = new HashSet<string>();
        foreach (var (term, termCodes) in NaicsMap)
            if (q.Contains(term))
                foreach (var c in termCodes)
                    if (seen.Add(c)) codes.Add(c);

        if (codes.Count == 0)
            codes.AddRange(NaicsMap.Values.SelectMany(x => x).Distinct());

        return codes;
    }

    private static string? GetStr(JsonElement elem, string key)
    {
        if (!elem.TryGetProperty(key, out var val)) return null;
        return val.ValueKind == JsonValueKind.Null ? null : val.ToString();
    }

    private static double? GetDouble(JsonElement elem, string key)
    {
        if (!elem.TryGetProperty(key, out var val)) return null;
        if (val.ValueKind == JsonValueKind.Null) return null;
        if (val.ValueKind == JsonValueKind.Number) return val.GetDouble();
        return null;
    }

    private static string SerializeError(string message, string source, bool retriable) =>
        JsonSerializer.Serialize(new { error = message, source, retriable });
}
