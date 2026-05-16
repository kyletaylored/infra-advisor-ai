using System.ComponentModel;
using System.Text.Json;
using Azure;
using Azure.Search.Documents;
using Azure.Search.Documents.Models;
using ModelContextProtocol.Server;

namespace InfraAdvisor.McpServer.Tools;

[McpServerToolType]
public sealed class WaterInfrastructureTool(IHttpClientFactory httpFactory, ILogger<WaterInfrastructureTool> logger)
{
    private const string EpaDefaultBase = "https://enviro.epa.gov/enviro/efservice";
    private const string TwdbDomain = "water";
    private const string TwdbDocType = "water_plan_project";
    private const string TwdbSource = "TWDB_2026_State_Water_Plan";
    private const string EpaSource = "EPA_SDWIS";

    [McpServerTool(Name = "get_water_infrastructure")]
    [Description(
        "Three water datasets behind one tool — dispatched by query_type. _source: " +
        "'EPA SDWIS' or 'TWDB'.\n" +
        "Coverage:\n" +
        "  - water_systems / violations → all US public water systems (EPA SDWIS)\n" +
        "  - water_plan_projects → Texas only (TWDB 2026 State Water Plan)\n" +
        "Use when the user asks:\n" +
        "  water_systems: public water system inventory; population served; system type " +
        "counts; CWS / NTNCWS / TNCWS breakdown.\n" +
        "  violations: open SDWA violations; Tier 1 / 2 / 3 violations; PWSIDs with " +
        "compliance problems; remediation candidates.\n" +
        "  water_plan_projects: TWDB recommended projects; desalination / aquifer / " +
        "reuse strategies; Texas regional water plans (regions A-P).\n" +
        "Do NOT use for: stormwater / wastewater treatment plants (use search_project_" +
        "knowledge or web search); individual water-quality test results; non-Texas " +
        "state water plans.\n" +
        "PWSID = Public Water System ID — 9-character identifier. Always cite PWSID " +
        "for individual systems.")]
    public async Task<string> GetWaterInfrastructureAsync(
        [Description("REQUIRED. 'water_systems' (EPA SDWIS inventory) | 'violations' (EPA SDWA violations) | 'water_plan_projects' (TWDB Texas projects).")] string query_type,
        [Description("2-letter state codes. Applies to water_systems/violations. For water_plan_projects use ['TX'] only — TWDB is Texas-specific.")] List<string>? states = null,
        [Description("County names (case-insensitive). E.g. ['Harris', 'Dallas', 'Travis'].")] List<string>? counties = null,
        [Description("TWDB planning region codes A-P (water_plan_projects only). Region H = Houston area; L = Llano Estacado; etc.")] List<string>? planning_regions = null,
        [Description("Project type filter for water_plan_projects. Common: 'desalination', 'aquifer_storage', 'reuse', 'new_supply', 'conservation'.")] List<string>? project_types = null,
        [Description("EPA system types: 'CWS' (community water system — residential), 'NTNCWS' (non-transient non-community — schools/factories), 'TNCWS' (transient — rest stops, parks).")] List<string>? system_types = null,
        [Description("water_systems / violations only. true = only systems with active violations; false = compliant only; omit = all.")] bool? has_violations = null,
        [Description("Minimum population served. Use 10000 to focus on systems serving major populations.")] int? min_population_served = null,
        [Description("Max results (1-200). Default 50.")] int limit = 50,
        CancellationToken cancellationToken = default)
    {
        if (query_type is "water_systems" or "violations")
        {
            var stateList = states ?? new List<string>();
            if (stateList.Count == 0)
                return SerializeError("At least one state must be provided for water_systems/violations queries.", "epa_sdwis", false);

            var results = await FetchEpaWaterSystems(stateList, system_types, has_violations, min_population_served, limit, cancellationToken);
            foreach (var r in results) r.TryAdd("_source", EpaSource);
            return JsonSerializer.Serialize(results);
        }

        if (query_type == "water_plan_projects")
        {
            var results = await FetchTwdbProjects(counties, planning_regions, project_types, limit, cancellationToken);
            foreach (var r in results) r.TryAdd("_source", TwdbSource);
            return JsonSerializer.Serialize(results);
        }

        return SerializeError($"Unknown query_type: '{query_type}'", "water_infrastructure", false);
    }

    private async Task<List<Dictionary<string, object?>>> FetchEpaWaterSystems(
        List<string> states, List<string>? systemTypes, bool? hasViolations,
        int? minPopulationServed, int limit, CancellationToken cancellationToken)
    {
        var baseUrl = (Environment.GetEnvironmentVariable("EPA_SDWIS_BASE_URL") ?? EpaDefaultBase).TrimEnd('/');
        var pwsTypes = systemTypes?.Count > 0 ? systemTypes : new List<string> { "CWS" };
        var results = new List<Dictionary<string, object?>>();
        var client = httpFactory.CreateClient();
        client.Timeout = TimeSpan.FromSeconds(30);

        foreach (var state in states)
        {
            foreach (var pwsType in pwsTypes)
            {
                var url = $"{baseUrl}/WATER_SYSTEM/STATE_CODE/{state}/PWS_TYPE_CODE/{pwsType}/JSON";
                try
                {
                    var resp = await client.GetAsync(url, cancellationToken);
                    if (resp.StatusCode == System.Net.HttpStatusCode.NotFound)
                    {
                        logger.LogWarning("EPA SDWIS 404 for state={State} pws_type={PwsType}", state, pwsType);
                        continue;
                    }
                    resp.EnsureSuccessStatusCode();
                    var json = await resp.Content.ReadAsStringAsync(cancellationToken);
                    using var doc = JsonDocument.Parse(json);
                    var root = doc.RootElement;

                    IEnumerable<JsonElement> records;
                    if (root.ValueKind == JsonValueKind.Array)
                        records = root.EnumerateArray();
                    else if (root.TryGetProperty("results", out var recs))
                        records = recs.EnumerateArray();
                    else
                        records = Array.Empty<JsonElement>();

                    foreach (var rec in records)
                    {
                        var normalised = NormaliseWaterSystem(rec);
                        if (minPopulationServed.HasValue)
                        {
                            var pop = normalised.TryGetValue("population_served", out var p) ? p as int? : null;
                            if (pop == null || pop < minPopulationServed.Value) continue;
                        }
                        results.Add(normalised);
                    }
                }
                catch (Exception ex) when (ex is not OperationCanceledException)
                {
                    logger.LogError(ex, "Error querying EPA SDWIS for state={State}", state);
                }
            }
        }

        // Attach violation counts if needed
        if (hasViolations.HasValue)
        {
            results = await AttachViolationCounts(results, baseUrl, cancellationToken);
            if (hasViolations.Value)
                results = results.Where(r => (r.TryGetValue("open_violation_count", out var v) ? v as int? ?? 0 : 0) > 0).ToList();
            else
                results = results.Where(r => (r.TryGetValue("open_violation_count", out var v) ? v as int? ?? 0 : 0) == 0).ToList();
        }

        return results.Take(limit).ToList();
    }

    private async Task<List<Dictionary<string, object?>>> AttachViolationCounts(
        List<Dictionary<string, object?>> systems, string baseUrl, CancellationToken cancellationToken)
    {
        var client = httpFactory.CreateClient();
        client.Timeout = TimeSpan.FromSeconds(30);

        foreach (var system in systems)
        {
            var pwsid = system.TryGetValue("pwsid", out var p) ? p?.ToString() : null;
            if (string.IsNullOrEmpty(pwsid))
            {
                system["open_violation_count"] = 0;
                continue;
            }

            var url = $"{baseUrl}/SDWA_VIOLATIONS/PWSID/{pwsid}/IS_HEALTH_BASED_IND/Y/JSON";
            try
            {
                var resp = await client.GetAsync(url, cancellationToken);
                if (resp.StatusCode == System.Net.HttpStatusCode.NotFound)
                {
                    system["open_violation_count"] = 0;
                    continue;
                }
                resp.EnsureSuccessStatusCode();
                var json = await resp.Content.ReadAsStringAsync(cancellationToken);
                using var doc = JsonDocument.Parse(json);
                var root = doc.RootElement;

                var violations = root.ValueKind == JsonValueKind.Array ? root.EnumerateArray().ToList() : new List<JsonElement>();
                var openCount = violations.Count(v =>
                {
                    var status = GetStr(v, "VIOLATION_STATUS") ?? GetStr(v, "violation_status") ?? "";
                    return status.ToUpperInvariant() is "OPEN" or "UNRESOLVED" or "";
                });
                system["open_violation_count"] = openCount;
            }
            catch
            {
                system["open_violation_count"] = null;
            }
        }

        return systems;
    }

    private static Dictionary<string, object?> NormaliseWaterSystem(JsonElement rec)
    {
        return new Dictionary<string, object?>
        {
            ["system_name"] = GetStr(rec, "PWS_NAME") ?? GetStr(rec, "pws_name") ?? "",
            ["pwsid"] = GetStr(rec, "PWSID") ?? GetStr(rec, "pwsid") ?? "",
            ["city"] = GetStr(rec, "CITY_NAME") ?? GetStr(rec, "city_name") ?? "",
            ["county"] = GetStr(rec, "COUNTY_SERVED") ?? GetStr(rec, "county_served") ?? "",
            ["state"] = GetStr(rec, "STATE_CODE") ?? GetStr(rec, "state_code") ?? "",
            ["population_served"] = SafeInt(GetStr(rec, "POPULATION_SERVED_COUNT") ?? GetStr(rec, "population_served_count")),
            ["primary_source_type"] = GetStr(rec, "PRIMARY_SOURCE_CODE") ?? GetStr(rec, "primary_source_code") ?? "",
            ["pws_type"] = GetStr(rec, "PWS_TYPE_CODE") ?? GetStr(rec, "pws_type_code") ?? "",
            ["open_violation_count"] = null,
            ["last_inspection_date"] = GetStr(rec, "LAST_INSPECTION_DATE") ?? GetStr(rec, "last_inspection_date"),
            ["_source"] = EpaSource,
            ["_retrieved_at"] = DateTime.UtcNow.ToString("o"),
        };
    }

    private async Task<List<Dictionary<string, object?>>> FetchTwdbProjects(
        List<string>? counties, List<string>? planningRegions, List<string>? projectTypes, int limit,
        CancellationToken cancellationToken = default)
    {
        var endpoint = Environment.GetEnvironmentVariable("AZURE_SEARCH_ENDPOINT") ?? "";
        var apiKey = Environment.GetEnvironmentVariable("AZURE_SEARCH_API_KEY") ?? "";
        var indexName = Environment.GetEnvironmentVariable("AZURE_SEARCH_INDEX_NAME") ?? "infra-advisor-knowledge";

        if (string.IsNullOrEmpty(endpoint) || string.IsNullOrEmpty(apiKey))
        {
            logger.LogError("Azure AI Search credentials not configured for TWDB query");
            return new List<Dictionary<string, object?>>
            {
                new()
                {
                    ["_source"] = TwdbSource,
                    ["error"] = "Azure AI Search endpoint or API key not configured.",
                    ["retriable"] = false,
                    ["content"] = "",
                }
            };
        }

        var searchClient = new SearchClient(new Uri(endpoint), indexName, new AzureKeyCredential(apiKey));

        var searchParts = new List<string> { "water plan project" };
        if (planningRegions?.Count > 0) searchParts.AddRange(planningRegions.Select(r => $"region {r}"));
        if (counties?.Count > 0) searchParts.AddRange(counties);
        if (projectTypes?.Count > 0) searchParts.AddRange(projectTypes);
        var searchText = string.Join(" ", searchParts);

        var odataFilter = $"domain eq '{TwdbDomain}' and document_type eq '{TwdbDocType}'";

        try
        {
            var options = new SearchOptions
            {
                Filter = odataFilter,
                Size = limit,
                IncludeTotalCount = true,
            };

            var response = await searchClient.SearchAsync<SearchDocument>(searchText, options, cancellationToken);
            var records = new List<Dictionary<string, object?>>();

            await foreach (var result in response.Value.GetResultsAsync())
            {
                records.Add(ParseTwdbChunk(result));
            }

            logger.LogInformation("TWDB AI Search returned {Count} records", records.Count);
            return records;
        }
        catch (OperationCanceledException)
        {
            throw;
        }
        catch (ObjectDisposedException odEx) when (cancellationToken.IsCancellationRequested)
        {
            throw new OperationCanceledException("TWDB search cancelled", odEx, cancellationToken);
        }
        catch (Exception ex)
        {
            var msg = ex.Message.ToLowerInvariant();
            var isIndexMissing = msg.Contains("not found") && msg.Contains("index");
            logger.LogError(ex, "Azure AI Search error for TWDB query");

            if (isIndexMissing)
            {
                return new List<Dictionary<string, object?>>
                {
                    new()
                    {
                        ["_source"] = TwdbSource,
                        ["error"] = "Azure AI Search index not found. Run the knowledge_base_init Airflow DAG (make run-dags) to create and populate the index before querying water plan projects.",
                        ["retriable"] = false,
                        ["content"] = "",
                    }
                };
            }

            return new List<Dictionary<string, object?>>
            {
                new()
                {
                    ["_source"] = TwdbSource,
                    ["error"] = $"Azure AI Search query failed: {ex.Message}",
                    ["retriable"] = true,
                    ["content"] = "",
                }
            };
        }
    }

    private static Dictionary<string, object?> ParseTwdbChunk(SearchResult<SearchDocument> result)
    {
        var doc = result.Document;
        var score = result.Score;

        var record = new Dictionary<string, object?>
        {
            ["content"] = doc.TryGetValue("content", out var c) ? c?.ToString() ?? "" : "",
            ["source"] = doc.TryGetValue("source", out var s) ? s?.ToString() ?? TwdbSource : TwdbSource,
            ["document_type"] = TwdbDocType,
            ["domain"] = TwdbDomain,
            ["score"] = score,
            ["source_url"] = doc.TryGetValue("source_url", out var su) ? su?.ToString() : null,
            ["_source"] = TwdbSource,
            ["_retrieved_at"] = DateTime.UtcNow.ToString("o"),
        };

        foreach (var field in new[] { "project_name", "county", "planning_region", "strategy_type", "estimated_cost", "decade_of_need", "water_user_group" })
        {
            if (doc.TryGetValue(field, out var val) && val != null)
                record[field] = val;
        }

        return record;
    }

    private static string? GetStr(JsonElement elem, string key)
    {
        if (!elem.TryGetProperty(key, out var val)) return null;
        return val.ValueKind == JsonValueKind.Null ? null : val.ToString();
    }

    private static int? SafeInt(string? value)
    {
        if (string.IsNullOrEmpty(value)) return null;
        return int.TryParse(value, out var i) ? i : null;
    }

    private static string SerializeError(string message, string source, bool retriable) =>
        JsonSerializer.Serialize(new { error = message, source, retriable });
}
