using System.ComponentModel;
using System.Text.Json;
using ModelContextProtocol.Server;

namespace InfraAdvisor.McpServer.Tools;

[McpServerToolType]
public sealed class DisasterHistoryTool(IHttpClientFactory httpFactory, ILogger<DisasterHistoryTool> logger)
{
    private const string OpenFemaUrl = "https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries";
    private const int FemaPageSize = 1000;

    [McpServerTool(Name = "get_disaster_history")]
    [Description("Query OpenFEMA for disaster declarations and public assistance data.")]
    public async Task<string> GetDisasterHistoryAsync(
        [Description("List of 2-letter state codes, e.g. ['TX', 'LA', 'MS']")] List<string>? states = null,
        [Description("FEMA incident type names, e.g. ['Flood', 'Hurricane']")] List<string>? incident_types = null,
        [Description("ISO date string — return declarations on or after this date")] string? date_from = null,
        [Description("ISO date string — return declarations on or before this date")] string? date_to = null,
        [Description("Client-side keyword filter applied to declarationTitle (case-insensitive, any match)")] List<string>? infrastructure_keywords = null,
        [Description("Maximum number of declarations to return (1-1000)")] int limit = 100,
        CancellationToken cancellationToken = default)
    {
        var retrievedAt = DateTime.UtcNow.ToString("o");
        var odataFilter = BuildODataFilter(states, incident_types, date_from, date_to);
        var results = new List<Dictionary<string, object?>>();
        var client = httpFactory.CreateClient();

        var baseParams = new List<(string, string)>
        {
            ("$format", "json"),
            ("$orderby", "declarationDate desc"),
            ("$top", Math.Min(limit, FemaPageSize).ToString()),
        };
        if (!string.IsNullOrEmpty(odataFilter))
            baseParams.Add(("$filter", odataFilter));

        try
        {
            int skip = 0;
            while (true)
            {
                var pageParams = new List<(string, string)>(baseParams);
                if (skip > 0) pageParams.Add(("$skip", skip.ToString()));

                var qs = string.Join("&", pageParams.Select(p => $"{Uri.EscapeDataString(p.Item1)}={Uri.EscapeDataString(p.Item2)}"));
                var url = $"{OpenFemaUrl}?{qs}";

                HttpResponseMessage resp;
                try
                {
                    resp = await client.GetAsync(url, cancellationToken);
                }
                catch (Exception ex)
                {
                    return SerializeError($"OpenFEMA request error: {ex.Message}", "openfema", retriable: true);
                }

                if (!resp.IsSuccessStatusCode)
                {
                    var body = await resp.Content.ReadAsStringAsync(cancellationToken);
                    var retriable = (int)resp.StatusCode is 429 or 500 or 502 or 503 or 504;
                    return SerializeError($"OpenFEMA HTTP error {(int)resp.StatusCode}: {body[..Math.Min(200, body.Length)]}", "openfema", retriable);
                }

                var pageJson = await resp.Content.ReadAsStringAsync(cancellationToken);
                using var doc = JsonDocument.Parse(pageJson);
                var root = doc.RootElement;

                var records = root.TryGetProperty("DisasterDeclarationsSummaries", out var recs)
                    ? recs.EnumerateArray().ToList()
                    : new List<JsonElement>();

                foreach (var rec in records)
                {
                    if (infrastructure_keywords?.Count > 0)
                    {
                        var title = rec.TryGetProperty("declarationTitle", out var t) ? t.GetString() : null;
                        if (!MatchesKeywords(title, infrastructure_keywords))
                            continue;
                    }

                    results.Add(NormaliseDeclaration(rec, retrievedAt));
                    if (results.Count >= limit) break;
                }

                if (results.Count >= limit || records.Count < FemaPageSize)
                    break;

                skip += FemaPageSize;
            }
        }
        catch (Exception ex)
        {
            logger.LogError(ex, "Unexpected error in get_disaster_history");
            return SerializeError($"Unexpected error: {ex.Message}", "openfema", retriable: false);
        }

        return JsonSerializer.Serialize(results);
    }

    private static string? BuildODataFilter(
        List<string>? states, List<string>? incidentTypes, string? dateFrom, string? dateTo)
    {
        var parts = new List<string>();

        if (states?.Count > 0)
        {
            var clauses = states.Select(s => $"state eq '{s.ToUpperInvariant()}'");
            var joined = string.Join(" or ", clauses);
            parts.Add(states.Count > 1 ? $"({joined})" : joined);
        }

        if (incidentTypes?.Count > 0)
        {
            var clauses = incidentTypes.Select(t => $"incidentType eq '{t}'");
            var joined = string.Join(" or ", clauses);
            parts.Add(incidentTypes.Count > 1 ? $"({joined})" : joined);
        }

        if (!string.IsNullOrEmpty(dateFrom))
            parts.Add($"declarationDate ge '{dateFrom}T00:00:00.000z'");

        if (!string.IsNullOrEmpty(dateTo))
            parts.Add($"declarationDate le '{dateTo}T23:59:59.999z'");

        return parts.Count > 0 ? string.Join(" and ", parts) : null;
    }

    private static bool MatchesKeywords(string? title, List<string> keywords)
    {
        if (string.IsNullOrEmpty(title)) return false;
        var lower = title.ToLowerInvariant();
        return keywords.Any(kw => lower.Contains(kw.ToLowerInvariant()));
    }

    private static Dictionary<string, object?> NormaliseDeclaration(JsonElement rec, string retrievedAt)
    {
        var ihDeclared = rec.TryGetProperty("ihProgramDeclared", out var ih) ? ih.ValueKind != JsonValueKind.Null ? (object?)ih.GetInt32() : null : null;
        var iaDeclared = rec.TryGetProperty("iaProgramDeclared", out var ia) ? ia.ValueKind != JsonValueKind.Null ? (object?)ia.GetInt32() : null : null;
        var paDeclared = rec.TryGetProperty("paProgramDeclared", out var pa) ? pa.ValueKind != JsonValueKind.Null ? (object?)pa.GetInt32() : null : null;
        var hmDeclared = rec.TryGetProperty("hmProgramDeclared", out var hm) ? hm.ValueKind != JsonValueKind.Null ? (object?)hm.GetInt32() : null : null;

        return new Dictionary<string, object?>
        {
            ["disaster_number"] = GetInt(rec, "disasterNumber"),
            ["declaration_type"] = GetStr(rec, "declarationType"),
            ["declaration_title"] = GetStr(rec, "declarationTitle"),
            ["incident_type"] = GetStr(rec, "incidentType"),
            ["state"] = GetStr(rec, "state"),
            ["designated_area"] = GetStr(rec, "designatedArea"),
            ["declaration_date"] = GetStr(rec, "declarationDate"),
            ["incident_begin_date"] = GetStr(rec, "incidentBeginDate"),
            ["incident_end_date"] = GetStr(rec, "incidentEndDate"),
            ["close_out_date"] = GetStr(rec, "closeOutDate"),
            ["fips_state_code"] = GetStr(rec, "fipsStateCode"),
            ["fips_county_code"] = GetStr(rec, "fipsCountyCode"),
            ["program_declared"] = new Dictionary<string, object?>
            {
                ["ih"] = ihDeclared,
                ["ia"] = iaDeclared,
                ["pa"] = paDeclared,
                ["hm"] = hmDeclared,
            },
            ["_source"] = "OpenFEMA",
            ["_retrieved_at"] = retrievedAt,
        };
    }

    private static string? GetStr(JsonElement elem, string key)
    {
        if (!elem.TryGetProperty(key, out var val)) return null;
        return val.ValueKind == JsonValueKind.Null ? null : val.ToString();
    }

    private static object? GetInt(JsonElement elem, string key)
    {
        if (!elem.TryGetProperty(key, out var val)) return null;
        if (val.ValueKind == JsonValueKind.Null) return null;
        if (val.ValueKind == JsonValueKind.Number && val.TryGetInt32(out var i)) return i;
        return null;
    }

    private static string SerializeError(string message, string source, bool retriable) =>
        JsonSerializer.Serialize(new { error = message, source, retriable });
}
