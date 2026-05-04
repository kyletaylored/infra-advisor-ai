namespace InfraAdvisor.AgentApi.Models;

public class AppState
{
    public bool McpConnected { get; set; }
    public bool LlmConnected { get; set; }
    public List<string> AvailableModels { get; set; } = new();
    public string DefaultModel => AvailableModels.FirstOrDefault() ?? "gpt-4.1-mini";
}
