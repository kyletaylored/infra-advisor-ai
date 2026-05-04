using System.ComponentModel;
using System.Net.Http.Json;
using System.Text.Json;
using ModelContextProtocol.Server;

namespace InfraAdvisor.McpServer.Tools;

[McpServerToolType]
public sealed class ProcurementOpportunitiesTool(IHttpClientFactory httpFactory, ILogger<ProcurementOpportunitiesTool> logger)
{
    private const string SamGovApiUrl = "https://api.sam.gov/opportunities/v2/search";
    private const string GrantsGovSearchUrl = "https://apply07.grants.gov/grantsws/rest/opportunities/search/";

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

    private static readonly HashSet<string> CfdaAllowlist = new()
        { "66.458", "66.468", "97.047", "20.933", "14.228", "12.106", "11.300" };

    [McpServerTool(Name = "get_procurement_opportunities")]
    [Description(
        "Search SAM.gov and grants.gov for active federal contract opportunities and grants. " +
        "Merges results from both sources sorted by deadline (soonest first). " +
        "Each result is tagged with _source: 'SAM.gov' or 'grants.gov'. " +
        "Requires SAMGOV_API_KEY env var. " +
        "opportunity_types: filter to 'contract', 'grant', or omit for both.")]
    public async Task<string> GetProcurementOpportunitiesAsync(
        [Description("Search query")] string query,
        [Description("State abbreviation or geography filter, e.g. 'TX'")] string? geography = null,
        [Description("NAICS codes to filter by, e.g. ['237310', '237110']")] List<string>? naics_codes = null,
        [Description("Minimum contract/grant value in USD")] int? min_value_usd = null,
        [Description("Maximum contract/grant value in USD")] int? max_value_usd = null,
        [Description("Filter to 'contract', 'grant', or omit for both")] List<string>? opportunity_types = null,
        [Description("Maximum number of results to return")] int limit = 20,
        CancellationToken cancellationToken = default)
    {
        var derivedNaics = naics_codes ?? DeriveNaics(query);
        var opTypes = opportunity_types ?? new List<string> { "contract", "grant" };
        var includeContracts = opTypes.Contains("contract");
        var includeGrants = opTypes.Contains("grant");

        var samTask = includeContracts
            ? FetchSamGov(query, geography, derivedNaics, limit, cancellationToken)
            : Task.FromResult<object>(new List<Dictionary<string, object?>>());

        var grantsTask = includeGrants
            ? FetchGrantsGov(query, limit, cancellationToken)
            : Task.FromResult<List<Dictionary<string, object?>>>(new List<Dictionary<string, object?>>());

        await Task.WhenAll(samTask, grantsTask);

        var samResult = samTask.Result;
        var grantsItems = grantsTask.Result;

        List<Dictionary<string, object?>> samItems = new();
        Dictionary<string, object?>? samError = null;

        if (samResult is List<Dictionary<string, object?>> list)
        {
            samItems = list;
        }
        else if (samResult is Dictionary<string, object?> dict)
        {
            if (dict.ContainsKey("error"))
                samError = dict;
            else if (dict.TryGetValue("results", out var r) && r is List<Dictionary<string, object?>> rl)
                samItems = rl;
        }

        var allResults = samItems.Concat(grantsItems).ToList();

        // Sort by deadline
        allResults.Sort((a, b) =>
        {
            var da = GetDeadlineKey(a);
            var db = GetDeadlineKey(b);
            return string.Compare(da, db, StringComparison.Ordinal);
        });

        if (allResults.Count == 0 && samError != null)
            return JsonSerializer.Serialize(samError);

        if (samError != null && allResults.Count > 0)
        {
            return JsonSerializer.Serialize(new Dictionary<string, object?>
            {
                ["results"] = allResults,
                ["_samgov_error"] = samError,
                ["_note"] = "Partial results: SAM.gov unavailable, showing grants.gov results only.",
            });
        }

        return JsonSerializer.Serialize(allResults);
    }

    private async Task<object> FetchSamGov(string query, string? geography, List<string> naicsCodes, int limit, CancellationToken cancellationToken)
    {
        var apiKey = Environment.GetEnvironmentVariable("SAMGOV_API_KEY") ?? "";
        if (string.IsNullOrEmpty(apiKey))
            return new Dictionary<string, object?> { ["error"] = "SAMGOV_API_KEY not configured", ["retriable"] = false };

        var (postedFrom, postedTo, _) = BuildDateRange(364);

        var paramPairs = new List<(string, string)>
        {
            ("limit", "25"),
            ("offset", "0"),
            ("ptype", "o"),
            ("ptype", "p"),
            ("ptype", "k"),
            ("ptype", "r"),
            ("postedFrom", postedFrom),
            ("postedTo", postedTo),
            ("api_key", apiKey),
        };

        foreach (var code in naicsCodes) paramPairs.Add(("ncode", code));
        if (!string.IsNullOrEmpty(geography)) paramPairs.Add(("state", geography));

        var qs = string.Join("&", paramPairs.Select(p => $"{Uri.EscapeDataString(p.Item1)}={Uri.EscapeDataString(p.Item2)}"));
        var url = $"{SamGovApiUrl}?{qs}";

        var client = httpFactory.CreateClient();
        client.Timeout = TimeSpan.FromSeconds(30);

        HttpResponseMessage resp;
        try
        {
            resp = await client.GetAsync(url, cancellationToken);
        }
        catch (Exception ex)
        {
            logger.LogWarning("SAM.gov request error: {Error}", ex.Message);
            return new Dictionary<string, object?> { ["error"] = $"SAM.gov request failed: {ex.Message}", ["source"] = "samgov", ["retriable"] = true };
        }

        var statusCode = (int)resp.StatusCode;

        if (statusCode == 400)
        {
            var errBody = await resp.Content.ReadAsStringAsync(cancellationToken);
            try
            {
                using var errDoc = JsonDocument.Parse(errBody);
                var errMsg = errDoc.RootElement.TryGetProperty("errorMessage", out var em) ? em.GetString() :
                             errDoc.RootElement.TryGetProperty("errorCode", out var ec) ? ec.ToString() : errBody;
                if (errMsg?.Contains("Date range") == true)
                    return new Dictionary<string, object?> { ["error"] = $"SAM.gov rejected the request: date range must be within 1 year. Raw message: {errMsg}", ["source"] = "samgov", ["retriable"] = false };
                return new Dictionary<string, object?> { ["error"] = $"SAM.gov API error 400: {errMsg}", ["source"] = "samgov", ["retriable"] = false };
            }
            catch
            {
                return new Dictionary<string, object?> { ["error"] = $"SAM.gov API error 400: {errBody}", ["source"] = "samgov", ["retriable"] = false };
            }
        }

        if (statusCode == 403)
            return new Dictionary<string, object?> { ["error"] = "SAM.gov API returned 403 — API key may need up to 24 hours to activate after registration at api.sam.gov", ["source"] = "samgov", ["retriable"] = false };

        if (statusCode >= 400)
            return new Dictionary<string, object?> { ["error"] = $"SAM.gov API error: HTTP {statusCode}", ["source"] = "samgov", ["retriable"] = statusCode >= 500 };

        var json = await resp.Content.ReadAsStringAsync(cancellationToken);
        using var doc = JsonDocument.Parse(json);
        var body = doc.RootElement;

        if (!body.TryGetProperty("opportunitiesData", out var oppsElem))
        {
            logger.LogWarning("SAM.gov response missing 'opportunitiesData' key");
            return new Dictionary<string, object?>
            {
                ["error"] = "SAM.gov response format unexpected — 'opportunitiesData' key missing",
                ["source"] = "samgov",
                ["retriable"] = false,
                ["response_keys"] = body.EnumerateObject().Select(p => p.Name).ToList(),
            };
        }

        var opps = oppsElem.EnumerateArray().ToList();
        if (opps.Count == 0)
        {
            return new Dictionary<string, object?>
            {
                ["results"] = new List<object>(),
                ["_note"] = $"No results found. NAICS codes queried: {string.Join(", ", naicsCodes)}",
            };
        }

        return opps.Select(opp => new Dictionary<string, object?>
        {
            ["id"] = GetStr(opp, "noticeId") ?? GetStr(opp, "solicitationNumber") ?? "",
            ["title"] = GetStr(opp, "title") ?? "",
            ["type"] = GetStr(opp, "type") ?? "",
            ["agency"] = GetStr(opp, "fullParentPathName") ?? GetStr(opp, "organizationName") ?? "",
            ["naics_code"] = GetStr(opp, "naicsCode") ?? "",
            ["posted_date"] = GetStr(opp, "postedDate") ?? "",
            ["responseDeadLine"] = GetStr(opp, "responseDeadLine") ?? GetStr(opp, "archiveDate") ?? "",
            ["award_value_usd"] = opp.TryGetProperty("award", out var award) && award.ValueKind != JsonValueKind.Null
                ? award.TryGetProperty("amount", out var amt) ? (object?)amt.GetDouble() : null
                : null,
            ["description"] = TruncateString(GetStr(opp, "description") ?? "", 500),
            ["url"] = GetStr(opp, "uiLink")
                ?? (opp.TryGetProperty("resourceLinks", out var links) && links.ValueKind == JsonValueKind.Array
                    ? links.EnumerateArray().FirstOrDefault().GetString() : null),
            ["place_of_performance"] = opp.TryGetProperty("placeOfPerformance", out var pop) && pop.ValueKind != JsonValueKind.Null
                ? GetStr(pop, "stateName") ?? ""
                : "",
            ["_source"] = "SAM.gov",
        }).ToList();
    }

    private async Task<List<Dictionary<string, object?>>> FetchGrantsGov(string query, int limit, CancellationToken cancellationToken)
    {
        var payload = new { keyword = query, oppStatuses = "forecasted|posted", rows = limit };

        var client = httpFactory.CreateClient();
        client.Timeout = TimeSpan.FromSeconds(20);

        try
        {
            var resp = await client.PostAsJsonAsync(GrantsGovSearchUrl, payload, cancellationToken);
            if ((int)resp.StatusCode >= 400)
            {
                logger.LogWarning("grants.gov API returned {StatusCode}", resp.StatusCode);
                return new List<Dictionary<string, object?>>();
            }

            var json = await resp.Content.ReadAsStringAsync(cancellationToken);
            using var doc = JsonDocument.Parse(json);
            var body = doc.RootElement;

            List<JsonElement> rawOpps;
            if (body.TryGetProperty("opportunities", out var o))
                rawOpps = o.EnumerateArray().ToList();
            else if (body.TryGetProperty("data", out var d))
                rawOpps = d.EnumerateArray().ToList();
            else
                rawOpps = new List<JsonElement>();

            var results = new List<Dictionary<string, object?>>();
            foreach (var opp in rawOpps)
            {
                var cfdaList = opp.TryGetProperty("cfdaList", out var cl) ? cl.EnumerateArray().ToList() : new List<JsonElement>();
                var hasAllowedCfda = cfdaList.Any(c =>
                    c.TryGetProperty("programNumber", out var pn) && CfdaAllowlist.Contains(pn.GetString() ?? ""));
                if (!hasAllowedCfda) continue;

                results.Add(new Dictionary<string, object?>
                {
                    ["id"] = GetStr(opp, "id") ?? "",
                    ["title"] = GetStr(opp, "title") ?? "",
                    ["agency"] = GetStr(opp, "agencyName") ?? "",
                    ["open_date"] = GetStr(opp, "openDate") ?? "",
                    ["closeDate"] = GetStr(opp, "closeDate") ?? "",
                    ["estimated_total_funding_usd"] = opp.TryGetProperty("estimatedTotalProgramFunding", out var etf) && etf.ValueKind == JsonValueKind.Number ? etf.GetDouble() : null,
                    ["expected_awards"] = opp.TryGetProperty("expectedNumberOfAwards", out var ena) && ena.ValueKind == JsonValueKind.Number ? ena.GetInt32() : null,
                    ["description"] = TruncateString(GetStr(opp, "description") ?? "", 500),
                    ["cfda_numbers"] = cfdaList.Select(c => GetStr(c, "programNumber")).Where(p => p != null).ToList(),
                    ["_source"] = "grants.gov",
                });
            }

            return results;
        }
        catch (Exception ex)
        {
            logger.LogWarning("grants.gov fetch error: {Error}", ex.Message);
            return new List<Dictionary<string, object?>>();
        }
    }

    private static List<string> DeriveNaics(string query)
    {
        var q = query.ToLowerInvariant();
        var codes = new List<string>();
        var seen = new HashSet<string>();
        foreach (var (term, termCodes) in NaicsMap)
        {
            if (q.Contains(term))
                foreach (var c in termCodes)
                    if (seen.Add(c)) codes.Add(c);
        }
        if (codes.Count == 0)
        {
            var allCodes = NaicsMap.Values.SelectMany(x => x).Distinct().ToList();
            codes.AddRange(allCodes);
        }
        return codes;
    }

    private static (string from, string to, bool clamped) BuildDateRange(int daysBack)
    {
        var today = DateTime.UtcNow.Date;
        var from = today.AddDays(-daysBack);
        var to = today;
        var clamped = false;
        if ((to - from).Days > 365)
        {
            to = from.AddDays(365);
            clamped = true;
        }
        return (from.ToString("MM/dd/yyyy"), to.ToString("MM/dd/yyyy"), clamped);
    }

    private static string GetDeadlineKey(Dictionary<string, object?> item)
    {
        if (item.TryGetValue("responseDeadLine", out var d) && d is string s && !string.IsNullOrEmpty(s)) return s;
        if (item.TryGetValue("closeDate", out var cd) && cd is string cs && !string.IsNullOrEmpty(cs)) return cs;
        return "";
    }

    private static string? GetStr(JsonElement elem, string key)
    {
        if (!elem.TryGetProperty(key, out var val)) return null;
        return val.ValueKind == JsonValueKind.Null ? null : val.GetString();
    }

    private static string TruncateString(string s, int maxLen) =>
        s.Length > maxLen ? s[..maxLen] : s;
}
