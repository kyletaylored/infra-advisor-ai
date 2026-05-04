using System.ComponentModel;
using System.Text.Json;
using ModelContextProtocol.Server;

namespace InfraAdvisor.McpServer.Tools;

[McpServerToolType]
public sealed class ErcotEnergyTool(IHttpClientFactory httpFactory, ILogger<ErcotEnergyTool> logger)
{
    private const string EsrChargingProduct = "rptesr-m/4_sec_esr_charging_mw";

    private static string ErcotBaseUrl =>
        Environment.GetEnvironmentVariable("ERCOT_API_BASE_URL")?.TrimEnd('/')
        ?? "https://api.ercot.com/api/public-data";

    [McpServerTool(Name = "get_ercot_energy_storage")]
    [Description(
        "Query ERCOT's public data API for Energy Storage Resource (ESR) data. " +
        "query_type must be exactly one of: " +
        "'charging_data' (4-second ESR charging MW time-series, default), " +
        "'products' (list available ERCOT public data product IDs). " +
        "time_from / time_to accept ISO-8601 strings e.g. '2024-06-01T00:00:00'. " +
        "This tool is Texas-specific — ERCOT covers ~90% of the Texas grid. " +
        "Use get_energy_infrastructure for multi-state EIA data.")]
    public async Task<string> GetErcotEnergyStorageAsync(
        [Description("Query type: 'charging_data' or 'products'")] string query_type = "charging_data",
        [Description("Start time ISO-8601 e.g. '2024-06-01T00:00:00'")] string? time_from = null,
        [Description("End time ISO-8601 e.g. '2024-06-01T01:00:00'")] string? time_to = null,
        [Description("Filter: minimum ESR charging MW")] double? min_charging_mw = null,
        [Description("Filter: maximum ESR charging MW")] double? max_charging_mw = null,
        [Description("Page number (1-based)")] int page = 1,
        [Description("Page size")] int size = 100,
        CancellationToken cancellationToken = default)
    {
        var apiKey = Environment.GetEnvironmentVariable("ERCOT_API_KEY") ?? "";
        if (string.IsNullOrEmpty(apiKey))
            return SerializeError("ERCOT_API_KEY environment variable is not set.", "ercot", false);

        var headers = new Dictionary<string, string>
        {
            ["Ocp-Apim-Subscription-Key"] = apiKey,
            ["Accept"] = "application/json",
        };

        if (query_type == "products")
            return await ListProductsAsync(headers, cancellationToken);

        return await QueryChargingDataAsync(headers, time_from, time_to, min_charging_mw, max_charging_mw, page, size, cancellationToken);
    }

    private async Task<string> ListProductsAsync(Dictionary<string, string> headers, CancellationToken cancellationToken)
    {
        var url = $"{ErcotBaseUrl}/";
        var client = httpFactory.CreateClient();
        client.Timeout = TimeSpan.FromSeconds(20);

        foreach (var (k, v) in headers) client.DefaultRequestHeaders.TryAddWithoutValidation(k, v);

        HttpResponseMessage resp;
        try
        {
            resp = await client.GetAsync(url, cancellationToken);
        }
        catch (TaskCanceledException)
        {
            return SerializeError("ERCOT API request timed out.", "ercot", true);
        }
        catch (Exception ex)
        {
            return SerializeError($"ERCOT API request failed: {ex.Message}", "ercot", true);
        }

        if ((int)resp.StatusCode >= 400)
        {
            var retriable = (int)resp.StatusCode >= 500;
            return SerializeError($"ERCOT API error: HTTP {(int)resp.StatusCode}", "ercot", retriable);
        }

        var json = await resp.Content.ReadAsStringAsync(cancellationToken);
        using var doc = JsonDocument.Parse(json);
        var body = doc.RootElement;

        List<JsonElement> products;
        if (body.ValueKind == JsonValueKind.Array)
            products = body.EnumerateArray().ToList();
        else if (body.TryGetProperty("data", out var d))
            products = d.EnumerateArray().ToList();
        else if (body.TryGetProperty("products", out var p))
            products = p.EnumerateArray().ToList();
        else
            products = new List<JsonElement>();

        var results = products.Select(p => new Dictionary<string, object?>
        {
            ["product_id"] = GetStr(p, "id") ?? GetStr(p, "productId"),
            ["name"] = GetStr(p, "name"),
            ["_source"] = "ERCOT_ESR",
        }).ToList();

        return JsonSerializer.Serialize(results);
    }

    private async Task<string> QueryChargingDataAsync(
        Dictionary<string, string> headers, string? timeFrom, string? timeTo,
        double? minChargingMw, double? maxChargingMw, int page, int size, CancellationToken cancellationToken)
    {
        var url = $"{ErcotBaseUrl}/{EsrChargingProduct}";
        var paramPairs = new List<(string, string)>
        {
            ("page", page.ToString()),
            ("size", size.ToString()),
            ("sort", "AGCExecTimeUTC"),
            ("dir", "desc"),
        };

        if (!string.IsNullOrEmpty(timeFrom)) paramPairs.Add(("AGCExecTimeFrom", timeFrom));
        if (!string.IsNullOrEmpty(timeTo)) paramPairs.Add(("AGCExecTimeTo", timeTo));
        if (minChargingMw.HasValue) paramPairs.Add(("ESRChargingMWFrom", minChargingMw.Value.ToString()));
        if (maxChargingMw.HasValue) paramPairs.Add(("ESRChargingMWTo", maxChargingMw.Value.ToString()));

        var qs = string.Join("&", paramPairs.Select(p => $"{Uri.EscapeDataString(p.Item1)}={Uri.EscapeDataString(p.Item2)}"));
        var fullUrl = $"{url}?{qs}";

        var client = httpFactory.CreateClient();
        client.Timeout = TimeSpan.FromSeconds(30);
        foreach (var (k, v) in headers) client.DefaultRequestHeaders.TryAddWithoutValidation(k, v);

        HttpResponseMessage resp;
        try
        {
            resp = await client.GetAsync(fullUrl, cancellationToken);
        }
        catch (TaskCanceledException)
        {
            return SerializeError("ERCOT API request timed out.", "ercot", true);
        }
        catch (Exception ex)
        {
            return SerializeError($"ERCOT API request failed: {ex.Message}", "ercot", true);
        }

        var statusCode = (int)resp.StatusCode;
        if (statusCode == 429) return SerializeError("ERCOT API rate limit exceeded.", "ercot", true);
        if (statusCode >= 400)
        {
            var errBody = await resp.Content.ReadAsStringAsync(cancellationToken);
            return SerializeError($"ERCOT API error: HTTP {statusCode} — {errBody[..Math.Min(200, errBody.Length)]}", "ercot", statusCode >= 500);
        }

        var json = await resp.Content.ReadAsStringAsync(cancellationToken);
        using var doc = JsonDocument.Parse(json);
        var body = doc.RootElement;

        List<JsonElement> rows;
        if (body.ValueKind == JsonValueKind.Array)
            rows = body.EnumerateArray().ToList();
        else if (body.TryGetProperty("data", out var d))
            rows = d.EnumerateArray().ToList();
        else if (body.TryGetProperty("rows", out var r))
            rows = r.EnumerateArray().ToList();
        else if (body.TryGetProperty("results", out var res))
            rows = res.EnumerateArray().ToList();
        else
            rows = new List<JsonElement>();

        if (rows.Count == 0)
        {
            logger.LogInformation("ERCOT ESR returned zero rows");
            return JsonSerializer.Serialize(Array.Empty<object>());
        }

        var results = rows.Select(NormaliseChargingRecord).ToList();
        logger.LogInformation("ERCOT ESR returned {Count} records", results.Count);
        return JsonSerializer.Serialize(results);
    }

    private static Dictionary<string, object?> NormaliseChargingRecord(JsonElement row)
    {
        return new Dictionary<string, object?>
        {
            ["agc_exec_time"] = GetStr(row, "AGCExecTime") ?? GetStr(row, "agcExecTime"),
            ["agc_exec_time_utc"] = GetStr(row, "AGCExecTimeUTC") ?? GetStr(row, "agcExecTimeUTC"),
            ["system_demand_mw"] = GetNumber(row, "systemDemand"),
            ["esr_charging_mw"] = GetNumber(row, "ESRChargingMW") ?? GetNumber(row, "esrChargingMW"),
            ["_source"] = "ERCOT_ESR",
            ["_retrieved_at"] = DateTime.UtcNow.ToString("o"),
        };
    }

    private static string? GetStr(JsonElement elem, string key)
    {
        if (!elem.TryGetProperty(key, out var val)) return null;
        return val.ValueKind == JsonValueKind.Null ? null : val.ToString();
    }

    private static object? GetNumber(JsonElement elem, string key)
    {
        if (!elem.TryGetProperty(key, out var val)) return null;
        if (val.ValueKind == JsonValueKind.Null) return null;
        if (val.ValueKind == JsonValueKind.Number) return val.TryGetInt64(out var i) ? (object)i : val.GetDouble();
        return null;
    }

    private static string SerializeError(string message, string source, bool retriable) =>
        JsonSerializer.Serialize(new { error = message, source, retriable });
}
