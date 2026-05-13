using System.Text.Json;
using Microsoft.Agents.AI;
using StackExchange.Redis;

namespace InfraAdvisor.AgentApi.Services;

// Redis-backed AgentSession persistence.
//
// MAF's AgentSession carries the conversation context (message history,
// memory provider state) for a single conversation. SerializeSessionAsync
// turns it into a JsonElement we can stash; DeserializeSessionAsync
// reconstitutes it.
//
// Keyed by the public conversation ID (browser-provided via the URL ?c=
// param, or falling back to the session UUID). Sessions persist for 24h
// past the last write — same TTL as the legacy MemoryService.
//
// On the agent path:
//   1. GetOrCreateAsync at the start of /chat → AgentSession (restored if
//      we've seen this conversationId before, else fresh).
//   2. agent.RunAsync(query, session) mutates the session.
//   3. SaveAsync at the end of /chat → Redis write-back.
public class AgentSessionStore
{
    private readonly IConnectionMultiplexer _redis;
    private readonly ILogger<AgentSessionStore> _logger;
    private const string KeyPrefix = "infra-advisor:agent-session";
    private const int TtlSeconds = 86400;

    public AgentSessionStore(IConnectionMultiplexer redis, ILogger<AgentSessionStore> logger)
    {
        _redis = redis;
        _logger = logger;
    }

    internal static string KeyFor(string conversationId) => $"{KeyPrefix}:{conversationId}";

    public async Task<AgentSession> GetOrCreateAsync(
        AIAgent agent, string conversationId, CancellationToken ct)
    {
        try
        {
            var db = _redis.GetDatabase();
            var json = await db.StringGetAsync(KeyFor(conversationId));
            if (!json.IsNullOrEmpty)
            {
                using var doc = JsonDocument.Parse((string)json!);
                return await agent.DeserializeSessionAsync(doc.RootElement);
            }
        }
        catch (Exception ex)
        {
            _logger.LogWarning(
                "Failed to restore agent session for {ConversationId}; starting fresh: {Error}",
                conversationId, ex.Message);
        }
        return await agent.CreateSessionAsync(ct);
    }

    public async Task SaveAsync(
        AIAgent agent, string conversationId, AgentSession session, CancellationToken ct)
    {
        try
        {
            var json = await agent.SerializeSessionAsync(session);
            var db = _redis.GetDatabase();
            await db.StringSetAsync(
                KeyFor(conversationId),
                json.GetRawText(),
                TimeSpan.FromSeconds(TtlSeconds));
        }
        catch (Exception ex)
        {
            _logger.LogWarning(
                "Failed to save agent session for {ConversationId}: {Error}",
                conversationId, ex.Message);
        }
    }
}
