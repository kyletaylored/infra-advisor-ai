using System.Text.Json;
using StackExchange.Redis;

namespace InfraAdvisor.AgentApi.Services;

public record ConversationMessage(string Role, string Content);

public class MemoryService
{
    private readonly IConnectionMultiplexer _redis;
    private readonly ILogger<MemoryService> _logger;
    private const string SessionPrefix = "infra-advisor:session";
    private const int SessionTtlSeconds = 86400;
    private const int WindowSize = 10; // exchange pairs

    private static readonly JsonSerializerOptions _jsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
    };

    public MemoryService(IConnectionMultiplexer redis, ILogger<MemoryService> logger)
    {
        _redis = redis;
        _logger = logger;
    }

    public async Task<List<ConversationMessage>> LoadHistoryAsync(string sessionId)
    {
        try
        {
            var db = _redis.GetDatabase();
            var raw = await db.StringGetAsync($"{SessionPrefix}:{sessionId}:memory");
            if (raw.IsNullOrEmpty) return new();
            var history = JsonSerializer.Deserialize<List<ConversationMessage>>(raw!, _jsonOptions) ?? new();
            return history.TakeLast(WindowSize * 2).ToList();
        }
        catch (Exception ex)
        {
            _logger.LogWarning("load_history failed for session={SessionId}: {Error}", sessionId, ex.Message);
            return new();
        }
    }

    public async Task SaveHistoryAsync(string sessionId, List<ConversationMessage> history)
    {
        try
        {
            var trimmed = history.TakeLast(WindowSize * 2).ToList();
            var json = JsonSerializer.Serialize(trimmed, _jsonOptions);
            var db = _redis.GetDatabase();
            await db.StringSetAsync($"{SessionPrefix}:{sessionId}:memory", json, TimeSpan.FromSeconds(SessionTtlSeconds));
        }
        catch (Exception ex)
        {
            _logger.LogWarning("save_history failed for session={SessionId}: {Error}", sessionId, ex.Message);
        }
    }

    public async Task AppendExchangeAsync(string sessionId, string humanMsg, string aiMsg)
    {
        var history = await LoadHistoryAsync(sessionId);
        history.Add(new ConversationMessage("human", humanMsg));
        history.Add(new ConversationMessage("ai", aiMsg));
        await SaveHistoryAsync(sessionId, history);
    }

    public async Task<bool> ClearSessionAsync(string sessionId)
    {
        try
        {
            var db = _redis.GetDatabase();
            var deleted = await db.KeyDeleteAsync(new RedisKey[] {
                $"{SessionPrefix}:{sessionId}:memory",
                $"{SessionPrefix}:{sessionId}:model",
            });
            return deleted > 0;
        }
        catch (Exception ex)
        {
            _logger.LogWarning("clear_session failed for session={SessionId}: {Error}", sessionId, ex.Message);
            return false;
        }
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
}
