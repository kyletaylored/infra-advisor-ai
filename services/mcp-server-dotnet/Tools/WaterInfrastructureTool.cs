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
        "Query water infrastructure data. " +
        "query_type must be exactly one of: " +
        "'water_systems' (EPA SDWIS public water system inventory), " +
        "'water_plan_projects' (TWDB 2026 State Water Plan recommended projects), " +
        "'violations' (EPA SDWIS health-based SDWA violations). " +
        "Use 'water_plan_projects' for TWDB plans, recommended projects, supply strategies, or regional water planning. " +
        "Use 'water_systems' or 'violations' for EPA compliance questions.")]
    public async Task<string> GetWaterInfrastructureAsync(
        [Description("Query type: 'water_systems', 'water_plan_projects', or 'violations'")] string query_type,
        [Description("List of 2-letter state codes")] List<string>? states = null,
        [Description("List of county names to filter results")] List<string>? counties = null,
        [Description("TWDB region codes A-P")] List<string>? planning_regions = null,
        [Description("Project types, e.g. 'desalination', 'aquifer_storage'")] List<string>? project_types = null,
        [Description("EPA system types: 'CWS', 'NTNCWS', 'TNCWS'")] List<string>? system_types = null,
        [Description("Filter by violation status: true=has violations, false=no violations")] bool? has_violations = null,
        [Description("Minimum population served")] int? min_population_served = null,
        [Description("Maximum number of results to return")] int limit = 50,
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
            var results = await FetchTwdbProjects(counties, planning_regions, project_types, limit);
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
        List<string>? counties, List<string>? planningRegions, List<string>? projectTypes, int limit)
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

            var response = await searchClient.SearchAsync<SearchDocument>(searchText, options);
            var records = new List<Dictionary<string, object?>>();

            await foreach (var result in response.Value.GetResultsAsync())
            {
                records.Add(ParseTwdbChunk(result));
            }

            logger.LogInformation("TWDB AI Search returned {Count} records", records.Count);
            return records;
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
