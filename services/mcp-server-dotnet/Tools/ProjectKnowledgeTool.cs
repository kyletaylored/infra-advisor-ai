using System.ComponentModel;
using System.Text.Json;
using Azure;
using Azure.AI.OpenAI;
using Azure.Search.Documents;
using Azure.Search.Documents.Models;
using ModelContextProtocol.Server;
using OpenAI.Embeddings;

namespace InfraAdvisor.McpServer.Tools;

[McpServerToolType]
public sealed class ProjectKnowledgeTool(ILogger<ProjectKnowledgeTool> logger)
{
    private const string EmbeddingModel = "text-embedding-ada-002";
    private const int MaxTopK = 20;

    [McpServerTool(Name = "search_project_knowledge")]
    [Description("Hybrid semantic + keyword search against the firm's internal knowledge base.")]
    public async Task<string> SearchProjectKnowledgeAsync(
        [Description("Search query")] string query,
        [Description("Filter by document types, e.g. ['water_plan_project', 'sow']")] List<string>? document_types = null,
        [Description("Filter by domains, e.g. ['water', 'transportation']")] List<string>? domains = null,
        [Description("Number of results to return (1-20, default 6)")] int top_k = 6,
        CancellationToken cancellationToken = default)
    {
        top_k = Math.Clamp(top_k, 1, MaxTopK);

        var endpoint = Environment.GetEnvironmentVariable("AZURE_SEARCH_ENDPOINT") ?? "";
        var apiKey = Environment.GetEnvironmentVariable("AZURE_SEARCH_API_KEY") ?? "";
        var indexName = Environment.GetEnvironmentVariable("AZURE_SEARCH_INDEX_NAME") ?? "infra-advisor-knowledge";

        if (string.IsNullOrEmpty(endpoint) || string.IsNullOrEmpty(apiKey))
            return SerializeError("Azure AI Search credentials not configured.", "azure_ai_search", false);

        // Step 1: Embed query
        float[] queryVector;
        try
        {
            queryVector = await EmbedQueryAsync(query, cancellationToken);
        }
        catch (Exception ex)
        {
            logger.LogError(ex, "Failed to embed query for search_project_knowledge");
            return SerializeError(ex.Message, "azure_openai", true);
        }

        // Step 2: Hybrid search
        var searchClient = new SearchClient(new Uri(endpoint), indexName, new AzureKeyCredential(apiKey));

        var odataFilter = BuildFilter(document_types, domains);

        var vectorQuery = new VectorizedQuery(queryVector)
        {
            KNearestNeighborsCount = top_k,
            Fields = { "content_vector" },
        };

        var options = new SearchOptions
        {
            Filter = odataFilter,
            Size = top_k,
            IncludeTotalCount = true,
            VectorSearch = new VectorSearchOptions { Queries = { vectorQuery } },
        };
        options.Select.Add("content");
        options.Select.Add("source");
        options.Select.Add("document_type");
        options.Select.Add("domain");
        options.Select.Add("source_url");
        options.Select.Add("chunk_index");

        try
        {
            var response = await searchClient.SearchAsync<SearchDocument>(query, options, cancellationToken);
            var chunks = new List<Dictionary<string, object?>>();
            int rank = 0;

            await foreach (var result in response.Value.GetResultsAsync())
            {
                chunks.Add(NormaliseChunk(result, rank++));
            }

            logger.LogInformation("search_project_knowledge: {Count} chunks returned", chunks.Count);
            return JsonSerializer.Serialize(chunks);
        }
        catch (Exception ex)
        {
            var msg = ex.Message.ToLowerInvariant();
            var isIndexMissing = msg.Contains("not found") && msg.Contains("index");
            logger.LogError(ex, "Azure AI Search hybrid search failed");

            if (isIndexMissing)
                return SerializeError(
                    "Azure AI Search index not found. Run the knowledge_base_init Airflow DAG (make run-dags) to create and populate the index before searching the knowledge base.",
                    "azure_ai_search", false);

            return SerializeError($"Azure AI Search query failed: {ex.Message}", "azure_ai_search", true);
        }
    }

    private static async Task<float[]> EmbedQueryAsync(string query, CancellationToken cancellationToken)
    {
        var azEndpoint = Environment.GetEnvironmentVariable("AZURE_OPENAI_ENDPOINT")
            ?? throw new InvalidOperationException("AZURE_OPENAI_ENDPOINT not set");
        var azApiKey = Environment.GetEnvironmentVariable("AZURE_OPENAI_API_KEY")
            ?? throw new InvalidOperationException("AZURE_OPENAI_API_KEY not set");
        var deployment = Environment.GetEnvironmentVariable("AZURE_OPENAI_EMBEDDING_DEPLOYMENT") ?? EmbeddingModel;

        var aoaiClient = new AzureOpenAIClient(new Uri(azEndpoint), new AzureKeyCredential(azApiKey));
        var embeddingClient = aoaiClient.GetEmbeddingClient(deployment);

        var result = await embeddingClient.GenerateEmbeddingAsync(query, cancellationToken: cancellationToken);
        return result.Value.ToFloats().ToArray();
    }

    private static string? BuildFilter(List<string>? documentTypes, List<string>? domains)
    {
        var clauses = new List<string>();

        if (documentTypes?.Count > 0)
        {
            var parts = string.Join(" or ", documentTypes.Select(dt => $"document_type eq '{dt}'"));
            clauses.Add($"({parts})");
        }

        if (domains?.Count > 0)
        {
            var parts = string.Join(" or ", domains.Select(d => $"domain eq '{d}'"));
            clauses.Add($"({parts})");
        }

        return clauses.Count > 0 ? string.Join(" and ", clauses) : null;
    }

    private static Dictionary<string, object?> NormaliseChunk(SearchResult<SearchDocument> result, int rank)
    {
        var doc = result.Document;
        return new Dictionary<string, object?>
        {
            ["content"] = doc.TryGetValue("content", out var c) ? c?.ToString() ?? "" : "",
            ["source"] = doc.TryGetValue("source", out var s) ? s?.ToString() ?? "" : "",
            ["document_type"] = doc.TryGetValue("document_type", out var dt) ? dt?.ToString() ?? "" : "",
            ["domain"] = doc.TryGetValue("domain", out var d) ? d?.ToString() ?? "" : "",
            ["score"] = result.Score ?? 0.0,
            ["source_url"] = doc.TryGetValue("source_url", out var su) ? su?.ToString() : null,
            ["chunk_index"] = doc.TryGetValue("chunk_index", out var ci) ? ci : rank,
            ["_retrieved_at"] = DateTime.UtcNow.ToString("o"),
        };
    }

    private static string SerializeError(string message, string source, bool retriable) =>
        JsonSerializer.Serialize(new { error = message, source, retriable });
}
