using StackExchange.Redis;

namespace InfraAdvisor.AgentApi.Services;

// Slim Redis-backed store for per-session metadata that doesn't live inside
// the MAF AgentSession itself.
//
// Conversation history is now owned by AgentSessionStore (which serializes
// the whole AgentSession to Redis via agent.SerializeSessionAsync). The
// only thing still managed here is the user's last-selected model per
// session, so subsequent /query calls without an explicit model body pick
// up the right deployment from where they left off.
public class MemoryService
{
    private readonly IConnectionMultiplexer _redis;
    private readonly ILogger<MemoryService> _logger;
    private const string SessionPrefix = "infra-advisor:session";
    private const int SessionTtlSeconds = 86400;

    public MemoryService(IConnectionMultiplexer redis, ILogger<MemoryService> logger)
    {
        _redis = redis;
        _logger = logger;
    }

    public async Task<string> GetSessionModelAsync(string sessionId)
    {
        try
        {
            var db = _redis.GetDatabase();
            var val = await db.StringGetAsync($"{SessionPrefix}:{sessionId}:model");
            return val.IsNullOrEmpty ? "gpt-4.1-mini" : val.ToString();
        }
        catch (Exception ex)
        {
            _logger.LogWarning("get_session_model failed for session={SessionId}: {Error}", sessionId, ex.Message);
            return "gpt-4.1-mini";
        }
    }

    public async Task SetSessionModelAsync(string sessionId, string model)
    {
        try
        {
            var db = _redis.GetDatabase();
            await db.StringSetAsync($"{SessionPrefix}:{sessionId}:model", model, TimeSpan.FromSeconds(SessionTtlSeconds));
        }
        catch (Exception ex)
        {
            _logger.LogWarning("set_session_model failed for session={SessionId}: {Error}", sessionId, ex.Message);
        }
    }

    public async Task<bool> ClearSessionAsync(string sessionId)
    {
        try
        {
            var db = _redis.GetDatabase();
            var deleted = await db.KeyDeleteAsync(new RedisKey[]
            {
                $"{SessionPrefix}:{sessionId}:model",
                AgentSessionStore.KeyFor(sessionId),
            });
            return deleted > 0;
        }
        catch (Exception ex)
        {
            _logger.LogWarning("clear_session failed for session={SessionId}: {Error}", sessionId, ex.Message);
            return false;
        }
    }
}
