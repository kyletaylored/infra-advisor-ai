using System.Text;
using System.Text.Json;

namespace InfraAdvisor.AgentApi.Services;

public class McpToolDefinition
{
    public string Name { get; set; } = "";
    public string Description { get; set; } = "";
    public JsonElement InputSchema { get; set; }
}

public class McpClientService
{
    private readonly HttpClient _http;
    private readonly ILogger<McpClientService> _logger;

    public McpClientService(HttpClient http, ILogger<McpClientService> logger)
    {
        _http = http;
        _logger = logger;
    }

    public async Task<List<McpToolDefinition>> ListToolsAsync(CancellationToken ct = default)
    {
        var payload = new { jsonrpc = "2.0", id = 1, method = "tools/list", @params = new { } };
        var response = await PostJsonRpcAsync(payload, ct);

        var tools = new List<McpToolDefinition>();
        if (response.TryGetProperty("result", out var result) &&
            result.TryGetProperty("tools", out var toolsArray))
        {
            foreach (var tool in toolsArray.EnumerateArray())
            {
                tools.Add(new McpToolDefinition
                {
                    Name = tool.GetProperty("name").GetString() ?? "",
                    Description = tool.TryGetProperty("description", out var d) ? d.GetString() ?? "" : "",
                    InputSchema = tool.TryGetProperty("inputSchema", out var s) ? s : default,
                });
            }
        }
        return tools;
    }

    public async Task<string> InvokeToolAsync(string toolName, JsonElement arguments, CancellationToken ct = default)
    {
        var payload = new
        {
            jsonrpc = "2.0",
            id = 2,
            method = "tools/call",
            @params = new
            {
                name = toolName,
                arguments = arguments,
            }
        };

        var response = await PostJsonRpcAsync(payload, ct);

        if (response.TryGetProperty("error", out var error))
            throw new InvalidOperationException($"MCP tool error: {error.GetRawText()}");

        if (response.TryGetProperty("result", out var result) &&
            result.TryGetProperty("content", out var content))
        {
            // Content is an array of {type, text} items; join text items
            var sb = new StringBuilder();
            foreach (var item in content.EnumerateArray())
            {
                if (item.TryGetProperty("text", out var text))
                    sb.Append(text.GetString());
            }
            return sb.ToString();
        }

        return "";
    }

    private async Task<JsonElement> PostJsonRpcAsync(object payload, CancellationToken ct)
    {
        var json = JsonSerializer.Serialize(payload);
        var content = new StringContent(json, Encoding.UTF8, "application/json");
        var resp = await _http.PostAsync("", content, ct);
        resp.EnsureSuccessStatusCode();
        var body = await resp.Content.ReadAsStringAsync(ct);
        using var doc = JsonDocument.Parse(body);
        return doc.RootElement.Clone();
    }
}
