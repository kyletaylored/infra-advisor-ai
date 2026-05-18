using Npgsql;

namespace InfraAdvisor.AgentApi.Observability;

// Wraps NpgsqlConnection so every command automatically receives the DBM
// sqlcommenter header. Callers use CreateCommand(sql) instead of the raw
// CreateCommand() + cmd.CommandText = DbmSqlComment.Wrap(sql) pattern.
// NpgsqlConnection is sealed, so delegation is the only option.
public sealed class DbmNpgsqlConnection(NpgsqlConnection inner) : IAsyncDisposable
{
    public NpgsqlCommand CreateCommand(string sql)
    {
        var cmd = inner.CreateCommand();
        cmd.CommandText = DbmSqlComment.Wrap(sql);
        return cmd;
    }

    // Returns a batch command with the sqlcommenter header pre-applied.
    // Caller adds parameters then appends to the NpgsqlBatch returned by CreateBatch().
    public NpgsqlBatchCommand CreateBatchCommand(string sql) =>
        new(DbmSqlComment.Wrap(sql));

    public NpgsqlBatch CreateBatch() => inner.CreateBatch();

    public async ValueTask DisposeAsync() => await inner.DisposeAsync();
}

public static class NpgsqlDataSourceExtensions
{
    public static async Task<DbmNpgsqlConnection> OpenDbmConnectionAsync(
        this NpgsqlDataSource ds, CancellationToken ct = default)
    {
        var conn = await ds.OpenConnectionAsync(ct);
        return new DbmNpgsqlConnection(conn);
    }
}
