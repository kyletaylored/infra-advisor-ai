using System.Diagnostics;
using Microsoft.Extensions.AI;

namespace InfraAdvisor.AgentApi.Services;

// In-process vector retrieval over a tiny AECOM best-practices corpus.
//
// Why in-process: the cluster Redis is plain redis:7-alpine, not Redis-Stack,
// so vector search isn't available. Standing up a separate vector store for
// a 6-doc corpus would be more infra than the showcase is worth. Cosine
// similarity over a List<> is fine at this scale and runs in microseconds.
//
// Span contract:
//   - retrieve_best_practices  (manual span, dd.llmobs.span.kind=retrieval)
//     └── embeddings          (framework span via IEmbeddingGenerator's
//                              .UseOpenTelemetry decorator — DD auto-classifies
//                              as embedding kind)
//
// Seeded lazily on first RetrieveAsync call so a transient embedding-endpoint
// outage at startup doesn't crash the process.
public class RetrievalService
{
    private readonly IEmbeddingGenerator<string, Embedding<float>> _embedder;
    private readonly ILogger<RetrievalService> _logger;
    private readonly List<SeededDocument> _docs = new();
    private readonly SemaphoreSlim _seedLock = new(1, 1);
    private bool _seeded;

    private static readonly ActivitySource ActivitySource =
        new(Observability.TelemetrySetup.ActivitySourceName);

    public RetrievalService(
        IEmbeddingGenerator<string, Embedding<float>> embedder,
        ILogger<RetrievalService> logger)
    {
        _embedder = embedder;
        _logger = logger;
    }

    public async Task<List<string>> RetrieveAsync(string query, int topK, CancellationToken ct)
    {
        using var activity = ActivitySource.StartActivity("retrieve_best_practices", ActivityKind.Internal);
        activity?.SetTag("dd.llmobs.span.kind", "retrieval");
        activity?.SetTag("input.value", query);
        activity?.SetTag("retrieval.top_k", topK);

        try
        {
            await EnsureSeededAsync(ct);

            if (_docs.Count == 0)
            {
                activity?.SetTag("retrieval.documents.count", 0);
                activity?.SetTag("retrieval.degraded", "true");
                return new List<string>();
            }

            // Query embedding — framework emits the embedding child span here.
            var queryEmbedding = await _embedder.GenerateAsync(new[] { query }, cancellationToken: ct);
            var queryVec = queryEmbedding[0].Vector.ToArray();

            var ranked = _docs
                .Select(d => (d.Title, d.Content, Score: CosineSimilarity(queryVec, d.Vector)))
                .OrderByDescending(x => x.Score)
                .Take(topK)
                .ToList();

            var output = ranked.Select(r => $"[{r.Title}] {r.Content}").ToList();
            activity?.SetTag("output.value", string.Join("\n\n", output));
            activity?.SetTag("retrieval.documents.count", ranked.Count);
            activity?.SetTag("retrieval.top_score", ranked[0].Score);

            return output;
        }
        catch (Exception ex)
        {
            _logger.LogWarning("Retrieval failed; agent will proceed without context: {Error}", ex.Message);
            activity?.SetTag("retrieval.degraded", "true");
            activity?.SetTag("error.type", ex.GetType().Name);
            return new List<string>();
        }
    }

    private async Task EnsureSeededAsync(CancellationToken ct)
    {
        if (_seeded) return;
        await _seedLock.WaitAsync(ct);
        try
        {
            if (_seeded) return;
            foreach (var bp in BestPracticesCorpus.All)
            {
                var emb = await _embedder.GenerateAsync(new[] { bp.Content }, cancellationToken: ct);
                _docs.Add(new SeededDocument(bp.Title, bp.Content, emb[0].Vector.ToArray()));
            }
            _seeded = true;
            _logger.LogInformation("RetrievalService seeded with {Count} best-practice docs", _docs.Count);
        }
        finally
        {
            _seedLock.Release();
        }
    }

    private static double CosineSimilarity(float[] a, float[] b)
    {
        if (a.Length != b.Length) return 0.0;
        double dot = 0, magA = 0, magB = 0;
        for (var i = 0; i < a.Length; i++)
        {
            dot += a[i] * b[i];
            magA += a[i] * a[i];
            magB += b[i] * b[i];
        }
        var denom = Math.Sqrt(magA) * Math.Sqrt(magB);
        return denom == 0 ? 0 : dot / denom;
    }

    private record SeededDocument(string Title, string Content, float[] Vector);
}

// AECOM-flavoured best-practices corpus. Short on purpose — enough doc
// diversity that retrieval picks different items for different question
// domains, but small enough to seed in <1s on startup.
internal static class BestPracticesCorpus
{
    public static readonly IReadOnlyList<(string Title, string Content)> All = new[]
    {
        ("Bridge Inspection Priorities",
         "FHWA NBI sufficiency ratings below 50 indicate a bridge eligible for federal " +
         "replacement funding. Sort inspection priorities by ascending sufficiency rating. " +
         "Flag scour-critical bridges (item 113 = 0,1,2,3) for immediate underwater inspection."),

        ("Water System Compliance Tiers",
         "EPA SDWA violations are tiered: Tier 1 (acute health) requires public notice " +
         "within 24 hours; Tier 2 (non-acute) within 30 days; Tier 3 (monitoring/reporting) " +
         "annually. PWSIDs with repeated Tier 1 violations are top remediation candidates."),

        ("Energy Grid Reliability Reserves",
         "ERCOT operating reserves below 2,300 MW trigger an Energy Emergency Alert. " +
         "Reserves between 2,300-2,500 MW = EEA Watch. Battery storage (ESR) discharge " +
         "during peak hours is the fastest-deploying mitigation resource."),

        ("Federal Procurement Timing",
         "SAM.gov solicitation cycles: pre-solicitation (~30 days), solicitation open " +
         "(15-45 days typical), Q&A window (10-15 days), award (~60-120 days post-close). " +
         "Always run get_contract_awards on incumbents before bidding new opportunities."),

        ("Disaster Recovery Coordination",
         "FEMA Stafford Act phases: Preparedness, Response, Recovery, Mitigation. " +
         "Public Assistance funding categories A-G; Categories C-G (permanent work) " +
         "require detailed damage descriptions and cost estimates within 60 days of " +
         "declaration."),

        ("Document Drafting Best Practices",
         "Always call search_project_knowledge before drafting an SOW or basis-of-design " +
         "memo — pulls firm-vetted templates and prior similar projects. For risk " +
         "summaries, structure as Top 5 risks ranked by likelihood × impact."),
    };
}
