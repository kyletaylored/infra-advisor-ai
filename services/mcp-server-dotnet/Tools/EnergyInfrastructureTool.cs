using System.ComponentModel;
using System.Text.Json;
using ModelContextProtocol.Server;

namespace InfraAdvisor.McpServer.Tools;

[McpServerToolType]
public sealed class EnergyInfrastructureTool(IHttpClientFactory httpFactory, ILogger<EnergyInfrastructureTool> logger)
{
    private const string EiaApiUrl = "https://api.eia.gov/v2/electricity/electric-power-operational-data/data/";

    private static readonly HashSet<string> ValidDataSeries = new() { "generation", "capacity", "fuel_mix" };

    private static readonly Dictionary<string, string> DataSeriesColumn = new()
    {
        ["generation"] = "generation",
        ["capacity"] = "capacity",
        ["fuel_mix"] = "generation",
    };

    [McpServerTool(Name = "get_energy_infrastructure")]
    [Description(
        "EIA — state-level annual electricity statistics. _source: 'EIA'. Requires " +
        "EIA_API_KEY.\n" +
        "Coverage: all 50 US states + DC. Annual data, multi-year history available.\n" +
        "Use when the user asks: how much electricity does <state> generate by fuel; " +
        "renewable energy share by state; installed capacity for solar / wind / gas; " +
        "energy mix trends over time; state-level resource planning context.\n" +
        "Do NOT use for: Texas ERCOT real-time grid data (use get_ercot_energy_storage); " +
        "individual power plants (EIA-860 plant-level not exposed here); transmission or " +
        "distribution data; ENERGY STAR or efficiency programs.\n" +
        "data_series semantics:\n" +
        "  'generation' (default) → MWh actually generated, broken out by fuel type\n" +
        "  'capacity' → MW of nameplate generating capacity installed\n" +
        "  'fuel_mix' → percentage share of generation by fuel\n" +
        "Common fuel codes: SUN=solar, WND=wind, NG=natural gas, COL=coal, NUC=nuclear, " +
        "HYC=conventional hydro, BIO=biomass, GEO=geothermal, PET=petroleum.")]
    public async Task<string> GetEnergyInfrastructureAsync(
        [Description("REQUIRED. 2-letter state codes, e.g. ['TX', 'CA']. Multiple states allowed.")] List<string> states,
        [Description("'generation' (MWh per fuel, default) | 'capacity' (MW installed) | 'fuel_mix' (% share).")] string data_series = "generation",
        [Description("Start year inclusive. Default: 5 years ago.")] int? year_from = null,
        [Description("End year inclusive. Default: latest available (~ current year minus 1).")] int? year_to = null,
        [Description("Fuel type codes filter. Common: ['SUN','WND','NG','COL','NUC','HYC']. Omit for all fuels.")] List<string>? fuel_types = null,
        CancellationToken cancellationToken = default)
    {
        if (!ValidDataSeries.Contains(data_series))
            return SerializeError($"Invalid data_series '{data_series}'. Must be one of: {string.Join(", ", ValidDataSeries.Order())}", "eia", false);

        var apiKey = Environment.GetEnvironmentVariable("EIA_API_KEY");
        if (string.IsNullOrEmpty(apiKey))
            return SerializeError("EIA_API_KEY environment variable is not set.", "eia", false);

        var dataCol = DataSeriesColumn[data_series];
        var paramPairs = new List<(string, string)>
        {
            ("api_key", apiKey),
            ("frequency", "annual"),
            ("data[]", dataCol),
            ("sort[0][column]", "period"),
            ("sort[0][direction]", "desc"),
            ("length", "5000"),
        };

        foreach (var state in states)
            paramPairs.Add(("facets[location][]", state));

        if (fuel_types != null)
            foreach (var ft in fuel_types)
                paramPairs.Add(("facets[fueltypeid][]", ft));

        if (year_from.HasValue) paramPairs.Add(("start", year_from.Value.ToString()));
        if (year_to.HasValue) paramPairs.Add(("end", year_to.Value.ToString()));

        var qs = string.Join("&", paramPairs.Select(p => $"{Uri.EscapeDataString(p.Item1)}={Uri.EscapeDataString(p.Item2)}"));
        var url = $"{EiaApiUrl}?{qs}";

        var client = httpFactory.CreateClient();
        client.Timeout = TimeSpan.FromSeconds(30);

        HttpResponseMessage resp;
        try
        {
            resp = await client.GetAsync(url, cancellationToken);
        }
        catch (TaskCanceledException)
        {
            return SerializeError("EIA API request timed out.", "eia", true);
        }
        catch (Exception ex)
        {
            return SerializeError($"EIA API request failed: {ex.Message}", "eia", true);
        }

        var statusCode = (int)resp.StatusCode;
        if (statusCode == 429) return SerializeError("EIA API rate limit exceeded.", "eia", true);
        if (statusCode >= 500) return SerializeError($"EIA API server error: HTTP {statusCode}", "eia", true);
        if (statusCode >= 400)
        {
            var errBody = await resp.Content.ReadAsStringAsync(cancellationToken);
            return SerializeError($"EIA API client error: HTTP {statusCode} — {errBody[..Math.Min(200, errBody.Length)]}", "eia", false);
        }

        var json = await resp.Content.ReadAsStringAsync(cancellationToken);
        using var doc = JsonDocument.Parse(json);

        List<JsonElement> rows;
        try
        {
            rows = doc.RootElement
                .GetProperty("response")
                .GetProperty("data")
                .EnumerateArray()
                .ToList();
        }
        catch
        {
            rows = new List<JsonElement>();
        }

        if (rows.Count == 0)
        {
            logger.LogInformation("EIA returned zero rows for states={States} series={Series}", string.Join(",", states), data_series);
            return JsonSerializer.Serialize(Array.Empty<object>());
        }

        var results = rows.Select(row => NormaliseRecord(row, data_series)).ToList();
        logger.LogInformation("EIA returned {Count} records", results.Count);
        return JsonSerializer.Serialize(results);
    }

    private static Dictionary<string, object?> NormaliseRecord(JsonElement row, string dataSeries)
    {
        var period = GetStr(row, "period") ?? "";
        var state = GetStr(row, "location") ?? GetStr(row, "stateid") ?? "";
        var fuelType = GetStr(row, "fueltypeid") ?? GetStr(row, "fuelTypeId") ?? "";
        var dataCol = DataSeriesColumn.GetValueOrDefault(dataSeries, "generation");

        double? rawValue = null;
        if (row.TryGetProperty(dataCol, out var rawElem) && rawElem.ValueKind == JsonValueKind.Number)
            rawValue = rawElem.GetDouble();
        else if (row.TryGetProperty(dataCol, out var rawStr) && double.TryParse(rawStr.ToString(), out var parsed))
            rawValue = parsed;

        var record = new Dictionary<string, object?>
        {
            ["state"] = state,
            ["year"] = period.Length >= 4 ? period[..4] : null,
            ["fuel_type"] = fuelType,
            ["_source"] = "EIA",
            ["_retrieved_at"] = DateTime.UtcNow.ToString("o"),
        };

        if (dataSeries is "generation" or "fuel_mix")
        {
            // EIA v2 reports generation in thousand MWh — multiply by 1000
            record["generation_mwh"] = rawValue.HasValue ? rawValue.Value * 1000 : null;
            record["units"] = "MWh";
        }
        else
        {
            record["capacity_mw"] = rawValue;
            record["units"] = "MW";
        }

        return record;
    }

    private static string? GetStr(JsonElement elem, string key)
    {
        if (!elem.TryGetProperty(key, out var val)) return null;
        return val.ValueKind == JsonValueKind.Null ? null : val.ToString();
    }

    private static string SerializeError(string message, string source, bool retriable) =>
        JsonSerializer.Serialize(new { error = message, source, retriable });
}
