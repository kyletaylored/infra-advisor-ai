using Microsoft.Agents.AI;
using Microsoft.Extensions.AI;

namespace InfraAdvisor.AgentApi.Services;

// Holds the current ChatClientAgent instance + rebuilds it when the
// underlying MCP tool list changes (after an McpClientHolder.RefreshAsync).
//
// Why a holder instead of a plain DI singleton: ChatClientAgent's
// ChatOptions.Tools list is captured at construction. To pick up a
// refreshed tool list after an MCP reconnect we must rebuild the agent.
// Tracking McpClientHolder.Generation lets us rebuild lazily — once per
// reconnect — rather than per request.
public class AgentHolder
{
    private readonly IChatClient _chatClient;
    private readonly McpClientHolder _mcpHolder;
    private readonly string _systemPrompt;
    private readonly string _agentName;
    private readonly string _otelSourceName;
    private readonly object _lock = new();

    private AIAgent? _agent;
    private long _builtForGeneration = -1;

    public AgentHolder(
        IChatClient chatClient,
        McpClientHolder mcpHolder,
        string systemPrompt,
        string agentName,
        string otelSourceName)
    {
        _chatClient = chatClient;
        _mcpHolder = mcpHolder;
        _systemPrompt = systemPrompt;
        _agentName = agentName;
        _otelSourceName = otelSourceName;
    }

    public async Task<AIAgent> GetAgentAsync(CancellationToken ct)
    {
        var tools = await _mcpHolder.GetToolsAsync(ct);
        var currentGen = _mcpHolder.Generation;

        lock (_lock)
        {
            if (_agent is not null && _builtForGeneration == currentGen)
                return _agent;
        }

        // Build the new agent outside the lock — UseOpenTelemetry chain
        // is cheap but not zero, and we don't want to block sibling
        // requests during construction.
        var fresh = new ChatClientAgent(
                _chatClient,
                new ChatClientAgentOptions
                {
                    Name = _agentName,
                    ChatOptions = new ChatOptions
                    {
                        Instructions = _systemPrompt,
                        Tools = tools,
                    },
                })
            .AsBuilder()
            .UseOpenTelemetry(sourceName: _otelSourceName,
                              configure: cfg => cfg.EnableSensitiveData = true)
            .Build();

        lock (_lock)
        {
            // Another concurrent caller may have built the same generation
            // already — prefer theirs to avoid orphaning an agent we're
            // about to replace.
            if (_agent is not null && _builtForGeneration == currentGen)
                return _agent;
            _agent = fresh;
            _builtForGeneration = currentGen;
            return _agent;
        }
    }
}
