using System.ComponentModel;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using ModelContextProtocol.Server;

namespace InfraAdvisor.McpServer.Tools;

// Web search for state / local procurement pages using Azure OpenAI's
// Responses API with the web_search_preview tool. Replaces the previous
// two-hop Tavily-search → gpt-4.1-nano-extraction pipeline with a single
// Azure OpenAI call: the Responses API runs the search, the model
// distills hits into structured procurement records via a JSON schema
// response format, and we return that JSON to the agent.
//
// Why: keeps the entire AI stack inside the Azure ecosystem (one vendor,
// one usage meter, OTel HttpClient already instrumented), removes the
// Tavily dependency entirely.
//
// Requirements:
//   AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY  — same secret already
//     used by the gpt-4.1-mini deployment
//   AZURE_OPENAI_DEPLOYMENT_NAME (default: gpt-4.1-mini) — must be a
//     deployment that supports web_search_preview (gpt-4o family or
//     gpt-4.1 family). gpt-4.1-nano does NOT support it.
//
// Endpoint contract:
//   POST {endpoint}/openai/v1/responses?api-version=preview
//   body: { model, input, tools=[{type:web_search_preview}],
//           text.format = json_schema(procurement_results) }
[McpServerToolType]
public sealed class WebProcurementSearchTool(
    IHttpClientFactory httpFactory,
    ILogger<WebProcurementSearchTool> logger)
{
    private static readonly Dictionary<string, string> SectorTerms = new()
    {
        ["transportation"] = "transportation infrastructure",
        ["water"] = "water treatment infrastructure",
        ["energy"] = "energy power infrastructure",
        ["buildings"] = "commercial building construction",
        ["environmental"] = "environmental remediation",
    };

    [McpServerTool(Name = "search_web_procurement")]
    [Description(
        "Search government websites for state/local RFPs, bond elections, and budget announcements. " +
        "Uses Azure OpenAI's Responses API with the web_search_preview tool — runs a live web search " +
        "and extracts structured procurement records in one call. " +
        "Requires AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, and a deployment that supports " +
        "web_search_preview (gpt-4o or gpt-4.1 family — not gpt-4.1-nano). " +
        "sector: 'transportation' | 'water' | 'energy' | 'buildings' | 'environmental'. " +
        "result_type: 'rfp' | 'bond' | 'budget' | 'award' | 'any'.")]
    public async Task<string> SearchWebProcurementAsync(
        [Description("Search query")] string query,
        [Description("Geography filter, e.g. 'Texas' or 'Harris County TX'")] string? geography = null,
        [Description("Infrastructure sector: 'transportation', 'water', 'energy', 'buildings', or 'environmental'")] string? sector = null,
        [Description("Result type: 'rfp', 'bond', 'budget', 'award', or 'any'")] string? result_type = null,
        [Description("Maximum number of results to return")] int limit = 8,
        CancellationToken cancellationToken = default)
    {
        var azEndpoint = Environment.GetEnvironmentVariable("AZURE_OPENAI_ENDPOINT") ?? "";
        var azApiKey = Environment.GetEnvironmentVariable("AZURE_OPENAI_API_KEY") ?? "";
        var deployment = Environment.GetEnvironmentVariable("AZURE_OPENAI_DEPLOYMENT_NAME") ?? "gpt-4.1-mini";

        if (string.IsNullOrEmpty(azEndpoint) || string.IsNullOrEmpty(azApiKey))
            return SerializeError("AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_API_KEY not configured", "azure_openai", false);

        var instructions = BuildInstructions(query, geography, sector, result_type, limit);
        logger.LogInformation(
            "web_procurement search query={Query} geography={Geography} sector={Sector} result_type={ResultType} limit={Limit}",
            query, geography, sector, result_type, limit);

        var payload = new
        {
            model = deployment,
            input = instructions,
            tools = new object[] { new { type = "web_search_preview" } },
            text = new
            {
                format = new
                {
                    type = "json_schema",
                    name = "procurement_results",
                    schema = ResultsSchema(),
                    strict = true,
                },
            },
        };

        var url = $"{azEndpoint.TrimEnd('/')}/openai/v1/responses?api-version=preview";
        var client = httpFactory.CreateClient();
        client.Timeout = TimeSpan.FromSeconds(60);

        HttpResponseMessage resp;
        try
        {
            using var req = new HttpRequestMessage(HttpMethod.Post, url)
            {
                Content = new StringContent(JsonSerializer.Serialize(payload), Encoding.UTF8,
                    new MediaTypeHeaderValue("application/json")),
            };
            req.Headers.TryAddWithoutValidation("api-key", azApiKey);
            resp = await client.SendAsync(req, cancellationToken);
        }
        catch (TaskCanceledException)
        {
            return SerializeError("Azure OpenAI Responses API request timed out", "azure_openai", true);
        }
        catch (Exception ex)
        {
            return SerializeError($"Azure OpenAI Responses API request failed: {ex.Message}", "azure_openai", true);
        }

        var statusCode = (int)resp.StatusCode;
        var body = await resp.Content.ReadAsStringAsync(cancellationToken);

        if (statusCode >= 400)
        {
            logger.LogWarning("web_procurement: Azure OpenAI HTTP {Status}: {Body}",
                statusCode, body.Length > 300 ? body[..300] : body);
            return SerializeError($"Azure OpenAI Responses API error: HTTP {statusCode}", "azure_openai",
                retriable: statusCode >= 500 || statusCode == 429);
        }

        // Responses API shape: { "output": [{type:"message", content:[{type:"output_text", text:"..."}]}] }
        // The structured JSON we asked for lives inside the message's output_text field.
        var jsonText = ExtractOutputText(body);
        if (string.IsNullOrWhiteSpace(jsonText))
        {
            logger.LogWarning("web_procurement: no output_text in response");
            return JsonSerializer.Serialize(Array.Empty<object>());
        }

        // Unwrap the procurement_results envelope; agents expect the bare array.
        try
        {
            using var doc = JsonDocument.Parse(jsonText);
            if (doc.RootElement.TryGetProperty("results", out var resultsEl) &&
                resultsEl.ValueKind == JsonValueKind.Array)
            {
                logger.LogInformation("web_procurement: returned {Count} results", resultsEl.GetArrayLength());
                return resultsEl.GetRawText();
            }
            return jsonText;
        }
        catch (JsonException ex)
        {
            logger.LogWarning("web_procurement: failed to parse model JSON: {Error}", ex.Message);
            return SerializeError("Model returned malformed JSON", "azure_openai", true);
        }
    }

    private static string BuildInstructions(
        string query, string? geography, string? sector, string? resultType, int limit)
    {
        var qualifiers = new List<string> { query };
        if (!string.IsNullOrEmpty(geography)) qualifiers.Add($"in {geography}");
        if (!string.IsNullOrEmpty(sector) && SectorTerms.TryGetValue(sector, out var sectorTerm))
            qualifiers.Add(sectorTerm);
        var typePhrase = resultType switch
        {
            "rfp" => "active requests for proposals (RFPs) and solicitations",
            "bond" => "bond elections and municipal bond initiatives",
            "budget" => "infrastructure budget and capital improvement plans",
            "award" => "recent procurement awards",
            _ => "procurement opportunities and announcements",
        };

        return
            $"Search the web for up to {limit} {typePhrase} matching: {string.Join(" ", qualifiers)}. " +
            "Prefer official government domains (.gov, .us, state/county/city procurement portals) " +
            "and recognized procurement aggregators (demandstar.com, bidnetdirect.com, bonfirehub.com). " +
            "For each hit, extract structured fields. " +
            "Use the source_url that links directly to the official announcement page. " +
            "Set confidence='high' only when the page clearly states the project title, " +
            "agency, and deadline; 'medium' when 1-2 fields are inferred; 'low' when most " +
            "details are missing. Return ONLY high or medium confidence results. " +
            "Set result_type appropriately (rfp/award/bond/budget/other). " +
            "If you cannot find any matching opportunities, return an empty results array.";
    }

    private static object ResultsSchema() => new
    {
        type = "object",
        additionalProperties = false,
        required = new[] { "results" },
        properties = new
        {
            results = new
            {
                type = "array",
                items = new
                {
                    type = "object",
                    additionalProperties = false,
                    required = new[]
                    {
                        "agency_name", "project_title", "project_description",
                        "estimated_value_usd", "deadline", "contact_email",
                        "source_url", "result_type", "confidence",
                    },
                    properties = new
                    {
                        agency_name = new { type = new[] { "string", "null" } },
                        project_title = new { type = new[] { "string", "null" } },
                        project_description = new
                        {
                            type = new[] { "string", "null" },
                            maxLength = 240,
                        },
                        estimated_value_usd = new { type = new[] { "integer", "null" } },
                        deadline = new
                        {
                            type = new[] { "string", "null" },
                            description = "ISO 8601 date (YYYY-MM-DD) or null when unknown",
                        },
                        contact_email = new { type = new[] { "string", "null" } },
                        source_url = new { type = "string" },
                        result_type = new { @enum = new[] { "rfp", "award", "bond", "budget", "other" } },
                        confidence = new { @enum = new[] { "high", "medium", "low" } },
                    },
                },
            },
        },
    };

    // Walks the Responses API output array and concatenates every
    // output_text content piece into a single string. The model returns
    // a single text item when using text.format=json_schema, but be
    // defensive against multi-item responses.
    private static string ExtractOutputText(string responseBody)
    {
        try
        {
            using var doc = JsonDocument.Parse(responseBody);
            if (!doc.RootElement.TryGetProperty("output", out var output) ||
                output.ValueKind != JsonValueKind.Array)
                return "";

            var sb = new StringBuilder();
            foreach (var item in output.EnumerateArray())
            {
                if (!item.TryGetProperty("content", out var content) ||
                    content.ValueKind != JsonValueKind.Array) continue;
                foreach (var part in content.EnumerateArray())
                {
                    if (part.TryGetProperty("type", out var t) &&
                        t.GetString() == "output_text" &&
                        part.TryGetProperty("text", out var text))
                        sb.Append(text.GetString());
                }
            }
            return sb.ToString();
        }
        catch
        {
            return "";
        }
    }

    private static string SerializeError(string message, string source, bool retriable) =>
        JsonSerializer.Serialize(new { error = message, source, retriable });
}
