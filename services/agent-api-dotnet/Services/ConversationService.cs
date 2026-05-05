using Npgsql;

namespace InfraAdvisor.AgentApi.Services;

public record ConversationSummary(
    string Id,
    string UserId,
    string Title,
    string? Model,
    string? Backend,
    string? CreatedAt,
    string? UpdatedAt,
    int MessageCount
);

public record ConversationDetail(
    string Id,
    string UserId,
    string Title,
    string? Model,
    string? Backend,
    string? CreatedAt,
    string? UpdatedAt,
    int MessageCount,
    IReadOnlyList<StoredMessage> Messages
);

public record StoredMessage(
    string Id,
    string ConversationId,
    string Role,
    string Content,
    IReadOnlyList<string> Sources,
    string? TraceId,
    string? SpanId,
    string? CreatedAt
);

public sealed class ConversationService
{
    private readonly NpgsqlDataSource? _ds;
    private readonly ILogger<ConversationService> _log;

    public ConversationService(ILogger<ConversationService> log)
    {
        _log = log;
        var url = Environment.GetEnvironmentVariable("DATABASE_URL");
        if (string.IsNullOrWhiteSpace(url))
        {
            _log.LogInformation("DATABASE_URL not set — conversation persistence disabled");
            _ds = null;
            return;
        }
        var builder = new NpgsqlDataSourceBuilder(url);
        _ds = builder.Build();
    }

    public async Task InitializeAsync()
    {
        if (_ds is null) return;
        try
        {
            await using var conn = await _ds.OpenConnectionAsync();
            await using var cmd = conn.CreateCommand();
            cmd.CommandText = """
                CREATE TABLE IF NOT EXISTS conversations (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT 'New Conversation',
                    model TEXT,
                    backend TEXT DEFAULT 'dotnet',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    conversation_id UUID REFERENCES conversations(id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    sources JSONB NOT NULL DEFAULT '[]',
                    trace_id TEXT,
                    span_id TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_conversations_user_id ON conversations(user_id);
                CREATE INDEX IF NOT EXISTS idx_messages_conversation_id ON messages(conversation_id);
                """;
            await cmd.ExecuteNonQueryAsync();
            _log.LogInformation("Conversation DB schema ready");
        }
        catch (Exception ex)
        {
            _log.LogWarning("InitializeAsync failed (conversations disabled): {Error}", ex.Message);
        }
    }

    public async Task<ConversationSummary?> CreateConversationAsync(
        string userId, string title = "New Conversation",
        string? model = null, string backend = "dotnet")
    {
        if (_ds is null) return null;
        await using var conn = await _ds.OpenConnectionAsync();
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            INSERT INTO conversations (user_id, title, model, backend)
            VALUES ($1, $2, $3, $4)
            RETURNING id, user_id, title, model, backend, created_at, updated_at
            """;
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(title);
        cmd.Parameters.AddWithValue(model is null ? DBNull.Value : (object)model);
        cmd.Parameters.AddWithValue(backend);
        await using var reader = await cmd.ExecuteReaderAsync();
        if (!await reader.ReadAsync()) return null;
        return new ConversationSummary(
            Id: reader.GetGuid(0).ToString(),
            UserId: reader.GetString(1),
            Title: reader.GetString(2),
            Model: reader.IsDBNull(3) ? null : reader.GetString(3),
            Backend: reader.IsDBNull(4) ? null : reader.GetString(4),
            CreatedAt: reader.GetDateTime(5).ToString("o"),
            UpdatedAt: reader.GetDateTime(6).ToString("o"),
            MessageCount: 0
        );
    }

    public async Task<IReadOnlyList<ConversationSummary>> ListConversationsAsync(string userId)
    {
        if (_ds is null) return [];
        await using var conn = await _ds.OpenConnectionAsync();
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            SELECT c.id, c.user_id, c.title, c.model, c.backend, c.created_at, c.updated_at,
                   COUNT(m.id) AS message_count
            FROM conversations c
            LEFT JOIN messages m ON m.conversation_id = c.id
            WHERE c.user_id = $1
            GROUP BY c.id
            ORDER BY c.updated_at DESC
            """;
        cmd.Parameters.AddWithValue(userId);
        var results = new List<ConversationSummary>();
        await using var reader = await cmd.ExecuteReaderAsync();
        while (await reader.ReadAsync())
        {
            results.Add(new ConversationSummary(
                Id: reader.GetGuid(0).ToString(),
                UserId: reader.GetString(1),
                Title: reader.GetString(2),
                Model: reader.IsDBNull(3) ? null : reader.GetString(3),
                Backend: reader.IsDBNull(4) ? null : reader.GetString(4),
                CreatedAt: reader.GetDateTime(5).ToString("o"),
                UpdatedAt: reader.GetDateTime(6).ToString("o"),
                MessageCount: (int)reader.GetInt64(7)
            ));
        }
        return results;
    }

    public async Task<ConversationDetail?> GetConversationAsync(string convId, string userId)
    {
        if (_ds is null) return null;
        await using var conn = await _ds.OpenConnectionAsync();

        ConversationSummary? summary = null;
        await using (var cmd = conn.CreateCommand())
        {
            cmd.CommandText = """
                SELECT c.id, c.user_id, c.title, c.model, c.backend, c.created_at, c.updated_at,
                       COUNT(m.id) AS message_count
                FROM conversations c
                LEFT JOIN messages m ON m.conversation_id = c.id
                WHERE c.id = $1 AND c.user_id = $2
                GROUP BY c.id
                """;
            cmd.Parameters.AddWithValue(Guid.Parse(convId));
            cmd.Parameters.AddWithValue(userId);
            await using var reader = await cmd.ExecuteReaderAsync();
            if (!await reader.ReadAsync()) return null;
            summary = new ConversationSummary(
                Id: reader.GetGuid(0).ToString(),
                UserId: reader.GetString(1),
                Title: reader.GetString(2),
                Model: reader.IsDBNull(3) ? null : reader.GetString(3),
                Backend: reader.IsDBNull(4) ? null : reader.GetString(4),
                CreatedAt: reader.GetDateTime(5).ToString("o"),
                UpdatedAt: reader.GetDateTime(6).ToString("o"),
                MessageCount: (int)reader.GetInt64(7)
            );
        }

        var messages = new List<StoredMessage>();
        await using (var cmd = conn.CreateCommand())
        {
            cmd.CommandText = """
                SELECT id, conversation_id, role, content, sources, trace_id, span_id, created_at
                FROM messages
                WHERE conversation_id = $1
                ORDER BY created_at ASC
                """;
            cmd.Parameters.AddWithValue(Guid.Parse(convId));
            await using var reader = await cmd.ExecuteReaderAsync();
            while (await reader.ReadAsync())
            {
                var sourcesJson = reader.IsDBNull(4) ? "[]" : reader.GetString(4);
                var sources = System.Text.Json.JsonSerializer.Deserialize<List<string>>(sourcesJson) ?? [];
                messages.Add(new StoredMessage(
                    Id: reader.GetGuid(0).ToString(),
                    ConversationId: reader.GetGuid(1).ToString(),
                    Role: reader.GetString(2),
                    Content: reader.GetString(3),
                    Sources: sources,
                    TraceId: reader.IsDBNull(5) ? null : reader.GetString(5),
                    SpanId: reader.IsDBNull(6) ? null : reader.GetString(6),
                    CreatedAt: reader.GetDateTime(7).ToString("o")
                ));
            }
        }

        return new ConversationDetail(
            Id: summary.Id,
            UserId: summary.UserId,
            Title: summary.Title,
            Model: summary.Model,
            Backend: summary.Backend,
            CreatedAt: summary.CreatedAt,
            UpdatedAt: summary.UpdatedAt,
            MessageCount: summary.MessageCount,
            Messages: messages
        );
    }

    public async Task<bool> DeleteConversationAsync(string convId, string userId)
    {
        if (_ds is null) return false;
        await using var conn = await _ds.OpenConnectionAsync();
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = "DELETE FROM conversations WHERE id = $1 AND user_id = $2";
        cmd.Parameters.AddWithValue(Guid.Parse(convId));
        cmd.Parameters.AddWithValue(userId);
        return await cmd.ExecuteNonQueryAsync() > 0;
    }

    public async Task SaveMessagesAsync(
        string convId, string userQuery, string aiAnswer,
        IReadOnlyList<string> sources, string? traceId, string? spanId)
    {
        if (_ds is null) return;
        try
        {
            var sourcesJson = System.Text.Json.JsonSerializer.Serialize(sources);
            await using var conn = await _ds.OpenConnectionAsync();
            await using var cmd = conn.CreateCommand();
            cmd.CommandText = """
                INSERT INTO messages (conversation_id, role, content, sources) VALUES ($1, 'user', $2, '[]'::jsonb);
                INSERT INTO messages (conversation_id, role, content, sources, trace_id, span_id)
                    VALUES ($1, 'assistant', $3, $4::jsonb, $5, $6);
                UPDATE conversations SET updated_at = NOW() WHERE id = $1;
                """;
            cmd.Parameters.AddWithValue(Guid.Parse(convId));
            cmd.Parameters.AddWithValue(userQuery);
            cmd.Parameters.AddWithValue(aiAnswer);
            cmd.Parameters.AddWithValue(sourcesJson);
            cmd.Parameters.AddWithValue(traceId is null ? DBNull.Value : (object)traceId);
            cmd.Parameters.AddWithValue(spanId is null ? DBNull.Value : (object)spanId);
            await cmd.ExecuteNonQueryAsync();
        }
        catch (Exception ex)
        {
            _log.LogWarning("SaveMessagesAsync failed for conv_id={ConvId}: {Error}", convId, ex.Message);
        }
    }
}
