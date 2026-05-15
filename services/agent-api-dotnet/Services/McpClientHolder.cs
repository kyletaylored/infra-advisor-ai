using Microsoft.Extensions.AI;
using ModelContextProtocol.Client;

namespace InfraAdvisor.AgentApi.Services;

// Manages the lifecycle of the MCP client + its tool list with on-demand
// reconnect.
//
// Why this exists: ModelContextProtocol.AspNetCore 1.3.0's HTTP transport
// is session-stateful. The server-side session is invalidated whenever
// mcp-server-dotnet restarts (rollout, OOM, AKS rebalance, image pull).
// After that, every tool call on the cached McpClient returns HTTP 404
// with "session expired" and the agent answer degrades.
//
// Previously this required manually `kubectl rollout restart deployment/
// agent-api-dotnet` every time mcp-server rolled. With this holder we
// catch the expired-session error at call time, dispose + recreate the
// client + tool list, and let the caller retry once transparently.
//
// Thread-safety: GetClientAsync / GetToolsAsync use a Lazy-style
// double-check. RefreshAsync serializes through a SemaphoreSlim so
// concurrent in-flight tool calls coalesce into one reconnect.
public class McpClientHolder : IAsyncDisposable
{
    private readonly string _serverUrl;
    private readonly string _clientName;
    private readonly ILogger<McpClientHolder> _logger;
    private readonly SemaphoreSlim _connectLock = new(1, 1);

    private McpClient? _client;
    private IList<AITool> _tools = Array.Empty<AITool>();
    private long _generation;  // bumped on every successful (re)connect

    public McpClientHolder(string serverUrl, string clientName, ILogger<McpClientHolder> logger)
    {
        _serverUrl = serverUrl;
        _clientName = clientName;
        _logger = logger;
    }

    // Generation increments on each successful (re)connect — callers (eg
    // AgentHolder) cache the agent against the generation and rebuild
    // when it changes.
    public long Generation => Interlocked.Read(ref _generation);

    public async Task<McpClient> GetClientAsync(CancellationToken ct)
    {
        if (_client is not null) return _client;
        await EnsureConnectedAsync(ct);
        return _client!;
    }

    public async Task<IList<AITool>> GetToolsAsync(CancellationToken ct)
    {
        if (_client is not null) return _tools;
        await EnsureConnectedAsync(ct);
        return _tools;
    }

    // Force a reconnect — disposes the current client and re-runs the
    // init handshake. Safe to call from inside an exception handler;
    // concurrent callers will see the same new client after one round
    // trip thanks to the connect lock.
    public async Task RefreshAsync(CancellationToken ct)
    {
        await _connectLock.WaitAsync(ct);
        try
        {
            await DisposeClientNoLockAsync();
            await ConnectNoLockAsync(ct);
        }
        finally
        {
            _connectLock.Release();
        }
    }

    private async Task EnsureConnectedAsync(CancellationToken ct)
    {
        await _connectLock.WaitAsync(ct);
        try
        {
            if (_client is not null) return;
            await ConnectNoLockAsync(ct);
        }
        finally
        {
            _connectLock.Release();
        }
    }

    private async Task ConnectNoLockAsync(CancellationToken ct)
    {
        var transport = new HttpClientTransport(new HttpClientTransportOptions
        {
            Endpoint = new Uri(_serverUrl),
            Name = _clientName,
        });
        var client = await McpClient.CreateAsync(transport, cancellationToken: ct);
        var listed = await client.ListToolsAsync(cancellationToken: ct);
        _client = client;
        _tools = [.. listed];
        Interlocked.Increment(ref _generation);
        _logger.LogInformation(
            "[mcp] connected to {Url} (gen {Generation}); loaded {Count} tool(s): {Tools}",
            _serverUrl, _generation, _tools.Count,
            string.Join(", ", listed.Select(t => t.Name)));
    }

    private async Task DisposeClientNoLockAsync()
    {
        var old = _client;
        _client = null;
        _tools = Array.Empty<AITool>();
        if (old is null) return;
        try { await old.DisposeAsync(); }
        catch (Exception ex)
        {
            _logger.LogDebug("Ignoring error disposing stale MCP client: {Error}", ex.Message);
        }
    }

    public async ValueTask DisposeAsync()
    {
        await _connectLock.WaitAsync();
        try { await DisposeClientNoLockAsync(); }
        finally { _connectLock.Release(); }
        _connectLock.Dispose();
    }
}
