using System.ComponentModel;
using System.Text.Json;
using System.Text.Json.Serialization;
using ModelContextProtocol.Server;

namespace InfraAdvisor.McpServer.Tools;

[McpServerToolType]
public sealed class BridgeConditionTool(IHttpClientFactory httpFactory, ILogger<BridgeConditionTool> logger)
{
    private const string NbiArcGisUrl =
        "https://services.arcgis.com/xOi1kZaI0eWDREZv/arcgis/rest/services/NTAD_National_Bridge_Inventory/FeatureServer/0/query";

    private const string NbiOutFields =
        "STRUCTURE_NUMBER_008,FACILITY_CARRIED_007,LOCATION_009,COUNTY_CODE_003,STATE_CODE_001," +
        "ADT_029,YEAR_ADT_030,DECK_COND_058,SUPERSTRUCTURE_COND_059,SUBSTRUCTURE_COND_060," +
        "BRIDGE_CONDITION,LOWEST_RATING,SCOUR_CRITICAL_113,DATE_OF_INSPECT_090,YEAR_BUILT_027,LAT_016,LONG_017";

    private const int ArcGisPageSize = 2000;

    private static readonly Dictionary<string, string> ConditionLabels = new()
    {
        ["9"] = "excellent",
        ["8"] = "very good",
        ["7"] = "good",
        ["6"] = "satisfactory",
        ["5"] = "fair",
        ["4"] = "poor",
        ["3"] = "serious",
        ["2"] = "critical",
        ["1"] = "imminent failure",
        ["0"] = "failed",
    };

    private static readonly Dictionary<string, string> BridgeConditionLabels = new()
    {
        ["G"] = "Good",
        ["F"] = "Fair",
        ["P"] = "Poor",
    };

    [McpServerTool(Name = "get_bridge_condition")]
    [Description(
        "FHWA National Bridge Inventory — every US public bridge over 20 ft (~617,000 " +
        "records). Returns structure-level condition data: deck / superstructure / " +
        "substructure ratings (0=failed → 9=excellent), overall BRIDGE_CONDITION " +
        "(Good/Fair/Poor), LOWEST_RATING (worst of the three), scour-critical flag, " +
        "ADT, location, year built. _source: 'FHWA NBI'.\n" +
        "Coverage: all US states + DC + Puerto Rico. Refreshed annually by FHWA.\n" +
        "Use when the user asks: structurally deficient bridges in <state/county>; " +
        "bridges with sufficiency rating under N; high-traffic bridges needing inspection; " +
        "scour-vulnerable bridges; oldest bridges in an area; bridge condition stats.\n" +
        "Do NOT use for: rail bridges (NBI is highway only); culverts under 20 ft span; " +
        "real-time inspection findings (data is annual); pedestrian-only bridges.\n" +
        "state_code is 2-CHARACTER FIPS STATE CODE — note leading zero for single-digit " +
        "states. Common values: AL=01, AZ=04, AR=05, CA=06, CO=08, FL=12, GA=13, IL=17, " +
        "LA=22, MS=28, NM=35, NY=36, NC=37, OK=40, TX=48, VA=51, WA=53. county_code is " +
        "the 3-character FIPS county code WITHIN that state (e.g. Harris County TX = 201)." +
        " Returns up to 200 rows sorted by ascending LOWEST_RATING (worst first).")]
    public async Task<string> GetBridgeConditionAsync(
        [Description("REQUIRED. 2-character FIPS state code with leading zero (e.g. '06' for CA, '48' for TX). NOT a 2-letter abbreviation.")] string state_code,
        [Description("3-character FIPS county code within the state (e.g. Harris County TX = '201'). Omit to query the whole state.")] string? county_code = null,
        [Description("Exact NBI structure number for a single-bridge lookup. Skip all other filters when set.")] string? structure_number = null,
        [Description("Minimum average daily traffic. Use 10000+ for major-highway-only filtering.")] int? min_adt = null,
        [Description("Upper bound on lowest NBI condition rating (0-9 scale). 4 = poor and below; 6 = fair and below. Returns bridges AT OR BELOW this rating.")] int? max_lowest_rating = null,
        [Description("True restricts results to FHWA-classified structurally deficient bridges (BRIDGE_CONDITION='P'). The fast path for 'deficient bridges' questions.")] bool structurally_deficient_only = false,
        [Description("ISO date string — informational only, not applied as a server-side filter on the upstream API.")] string? last_inspection_before = null,
        [Description("ArcGIS ORDER BY clause. Default sorts worst bridges first ('LOWEST_RATING ASC').")] string order_by = "LOWEST_RATING ASC",
        [Description("Max bridges to return (1-200). Default 50.")] int limit = 50,
        CancellationToken cancellationToken = default)
    {
        var retrievedAt = DateTime.UtcNow.ToString("o");
        var where = BuildWhereClause(state_code, county_code, structure_number, min_adt, max_lowest_rating, structurally_deficient_only);

        var results = new List<Dictionary<string, object?>>();
        var client = httpFactory.CreateClient();

        try
        {
            int offset = 0;
            while (true)
            {
                var queryParams = new Dictionary<string, string>
                {
                    ["where"] = where,
                    ["outFields"] = NbiOutFields,
                    ["orderByFields"] = order_by,
                    ["resultOffset"] = offset.ToString(),
                    ["resultRecordCount"] = ArcGisPageSize.ToString(),
                    ["f"] = "json",
                    ["returnGeometry"] = "false",
                };

                var qs = string.Join("&", queryParams.Select(kv => $"{Uri.EscapeDataString(kv.Key)}={Uri.EscapeDataString(kv.Value)}"));
                var url = $"{NbiArcGisUrl}?{qs}";

                HttpResponseMessage resp;
                try
                {
                    resp = await client.GetAsync(url, cancellationToken);
                }
                catch (Exception ex)
                {
                    return SerializeError($"BTS ArcGIS request error: {ex.Message}", "bts_arcgis", retriable: true);
                }

                if (!resp.IsSuccessStatusCode)
                {
                    var body = await resp.Content.ReadAsStringAsync(cancellationToken);
                    var retriable = (int)resp.StatusCode is 429 or 500 or 502 or 503 or 504;
                    return SerializeError($"BTS ArcGIS HTTP error {(int)resp.StatusCode}: {body[..Math.Min(200, body.Length)]}", "bts_arcgis", retriable);
                }

                var pageJson = await resp.Content.ReadAsStringAsync(cancellationToken);
                using var doc = JsonDocument.Parse(pageJson);
                var root = doc.RootElement;

                if (root.TryGetProperty("error", out var arcErr))
                {
                    var code = arcErr.TryGetProperty("code", out var c) ? c.ToString() : "unknown";
                    var msg = arcErr.TryGetProperty("message", out var m) ? m.GetString() : "unknown";
                    return SerializeError($"ArcGIS error {code}: {msg}", "bts_arcgis", retriable: false);
                }

                var features = root.TryGetProperty("features", out var feats) ? feats : default;
                var featureCount = 0;

                if (features.ValueKind == JsonValueKind.Array)
                {
                    foreach (var feat in features.EnumerateArray())
                    {
                        var attrs = feat.TryGetProperty("attributes", out var a) ? a : default;
                        if (attrs.ValueKind != JsonValueKind.Object) continue;

                        results.Add(NormaliseFeature(attrs, retrievedAt));
                        featureCount++;

                        if (results.Count >= limit) break;
                    }
                }

                if (results.Count >= limit || featureCount < ArcGisPageSize)
                    break;

                offset += ArcGisPageSize;
            }
        }
        catch (Exception ex)
        {
            logger.LogError(ex, "Unexpected error in get_bridge_condition");
            return SerializeError($"Unexpected error: {ex.Message}", "bts_arcgis", retriable: false);
        }

        return JsonSerializer.Serialize(results.Take(limit).ToList());
    }

    private static string BuildWhereClause(
        string stateCode, string? countyCode, string? structureNumber,
        int? minAdt, int? maxLowestRating, bool structurallyDeficientOnly)
    {
        var clauses = new List<string> { $"STATE_CODE_001='{stateCode}'" };
        if (!string.IsNullOrEmpty(countyCode)) clauses.Add($"COUNTY_CODE_003='{countyCode}'");
        if (!string.IsNullOrEmpty(structureNumber)) clauses.Add($"STRUCTURE_NUMBER_008='{structureNumber}'");
        if (minAdt.HasValue) clauses.Add($"ADT_029>={minAdt}");
        if (maxLowestRating.HasValue) clauses.Add($"LOWEST_RATING<={maxLowestRating}");
        if (structurallyDeficientOnly) clauses.Add("BRIDGE_CONDITION='P'");
        return string.Join(" AND ", clauses);
    }

    private static string? DecodeCondition(JsonElement elem)
    {
        if (elem.ValueKind == JsonValueKind.Null || elem.ValueKind == JsonValueKind.Undefined) return null;
        var key = elem.ToString().Trim();
        return ConditionLabels.TryGetValue(key, out var label) ? label : null;
    }

    private static Dictionary<string, object?> NormaliseFeature(JsonElement attrs, string retrievedAt)
    {
        var bridgeCond = GetString(attrs, "BRIDGE_CONDITION");
        var deckCode = attrs.TryGetProperty("DECK_COND_058", out var dk) ? dk : default;
        var supCode = attrs.TryGetProperty("SUPERSTRUCTURE_COND_059", out var sp) ? sp : default;
        var subCode = attrs.TryGetProperty("SUBSTRUCTURE_COND_060", out var sb) ? sb : default;

        return new Dictionary<string, object?>
        {
            ["structure_number"] = GetString(attrs, "STRUCTURE_NUMBER_008"),
            ["facility_carried"] = GetString(attrs, "FACILITY_CARRIED_007"),
            ["location"] = GetString(attrs, "LOCATION_009"),
            ["state_code"] = GetString(attrs, "STATE_CODE_001"),
            ["county_code"] = GetString(attrs, "COUNTY_CODE_003"),
            ["adt"] = GetNumber(attrs, "ADT_029"),
            ["year_adt"] = GetNumber(attrs, "YEAR_ADT_030"),
            ["deck_condition_code"] = GetRaw(attrs, "DECK_COND_058"),
            ["deck_condition"] = DecodeCondition(deckCode),
            ["superstructure_condition_code"] = GetRaw(attrs, "SUPERSTRUCTURE_COND_059"),
            ["superstructure_condition"] = DecodeCondition(supCode),
            ["substructure_condition_code"] = GetRaw(attrs, "SUBSTRUCTURE_COND_060"),
            ["substructure_condition"] = DecodeCondition(subCode),
            ["structurally_deficient"] = bridgeCond == "P",
            ["bridge_condition_category"] = bridgeCond != null && BridgeConditionLabels.TryGetValue(bridgeCond, out var bl) ? bl : null,
            ["lowest_rating"] = GetNumber(attrs, "LOWEST_RATING"),
            ["scour_critical"] = GetRaw(attrs, "SCOUR_CRITICAL_113"),
            ["last_inspection_date"] = GetString(attrs, "DATE_OF_INSPECT_090"),
            ["year_built"] = GetNumber(attrs, "YEAR_BUILT_027"),
            ["latitude"] = GetDouble(attrs, "LAT_016"),
            ["longitude"] = GetDouble(attrs, "LONG_017"),
            ["_source"] = "FHWA NBI",
            ["_retrieved_at"] = retrievedAt,
        };
    }

    private static string? GetString(JsonElement elem, string key)
    {
        if (!elem.TryGetProperty(key, out var val)) return null;
        return val.ValueKind == JsonValueKind.Null ? null : val.ToString();
    }

    private static object? GetRaw(JsonElement elem, string key)
    {
        if (!elem.TryGetProperty(key, out var val)) return null;
        return val.ValueKind switch
        {
            JsonValueKind.Null => null,
            JsonValueKind.Number => val.TryGetInt64(out var i) ? (object)i : val.GetDouble(),
            _ => val.ToString(),
        };
    }

    private static object? GetNumber(JsonElement elem, string key)
    {
        if (!elem.TryGetProperty(key, out var val)) return null;
        if (val.ValueKind == JsonValueKind.Null) return null;
        if (val.ValueKind == JsonValueKind.Number)
            return val.TryGetInt64(out var i) ? (object)i : val.GetDouble();
        if (int.TryParse(val.ToString(), out var parsed)) return parsed;
        return null;
    }

    private static double? GetDouble(JsonElement elem, string key)
    {
        if (!elem.TryGetProperty(key, out var val)) return null;
        if (val.ValueKind == JsonValueKind.Null) return null;
        if (val.ValueKind == JsonValueKind.Number) return val.GetDouble();
        if (double.TryParse(val.ToString(), out var d)) return d;
        return null;
    }

    private static string SerializeError(string message, string source, bool retriable) =>
        JsonSerializer.Serialize(new { error = message, source, retriable });
}
