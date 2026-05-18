using System.Diagnostics;
using System.Globalization;
using System.Text;

namespace InfraAdvisor.AgentApi.Observability;

// SQL-comment injector for DBM <-> APM service-level correlation.
//
// Background
// ----------
// OpenTelemetry's Npgsql instrumentation does NOT inject the Datadog-specific
// sqlcommenter tags (dddbs, dde, ddps, ddpv, traceparent). Without them, DBM's
// "Calling Services" panel stays empty for OTel-instrumented apps — you only
// get statement-level (hash-based) correlation. Datadog's docs explicitly
// recommend prepending these comments manually for pure-OTel apps:
// https://docs.datadoghq.com/database_monitoring/connect_dbm_and_apm/?tab=otel
//
// Format
// ------
// /*dddbs='<service>',dde='<env>',ddps='<service>',ddpv='<version>',
//   traceparent='00-<traceid>-<spanid>-<flags>'*/ SELECT ...
//
// The comment must come at the START of the statement — DBM's parser looks
// for a leading sqlcommenter block; trailing comments are ignored.
//
// The traceparent piece is what links a specific DBM query sample to the
// exact /query span that originated it, so the APM trace's "Database" tab
// shows the slow query and the DBM sample's "Trace" link round-trips back.
public static class DbmSqlComment
{
    private static readonly string _staticTags;

    static DbmSqlComment()
    {
        // Read once at startup. DD_SERVICE / DD_ENV / DD_VERSION are set by
        // K8s on every deployment via the standard tags.datadoghq.com labels,
        // so missing values here = misconfigured pod, not normal.
        var service = Environment.GetEnvironmentVariable("DD_SERVICE")
                   ?? Environment.GetEnvironmentVariable("OTEL_SERVICE_NAME")
                   ?? "unknown-service";
        var env = Environment.GetEnvironmentVariable("DD_ENV") ?? "dev";
        var version = Environment.GetEnvironmentVariable("DD_VERSION") ?? "latest";

        // Static portion — service name, env, version don't change at runtime.
        // Only traceparent (per-request) is appended dynamically below.
        _staticTags = $"dddbs='{Escape(service)}',dde='{Escape(env)}',"
                    + $"ddps='{Escape(service)}',ddpv='{Escape(version)}'";
    }

    // Returns the original SQL with a leading DBM sqlcommenter block.
    // If there's no active OTel Activity (e.g. background startup query),
    // the traceparent is omitted but the service tags still flow through.
    public static string Wrap(string sql)
    {
        var sb = new StringBuilder("/*");
        sb.Append(_staticTags);

        var activity = Activity.Current;
        if (activity is not null && activity.IsAllDataRequested)
        {
            // W3C traceparent: 00-<32 hex traceid>-<16 hex spanid>-<2 hex flags>
            // ActivityTraceFlags is byte: Recorded=1, NotRecorded=0.
            var flags = ((byte)activity.ActivityTraceFlags).ToString("x2", CultureInfo.InvariantCulture);
            sb.Append(",traceparent='00-")
              .Append(activity.TraceId.ToHexString())
              .Append('-')
              .Append(activity.SpanId.ToHexString())
              .Append('-')
              .Append(flags)
              .Append('\'');
        }

        sb.Append("*/ ");
        sb.Append(sql);
        return sb.ToString();
    }

    // Single quotes break the sqlcommenter parser — replace defensively even
    // though service/env/version values are never user-controlled in our setup.
    private static string Escape(string s) => s.Replace("'", "");
}
