# Parity audit, AI Guard rollout, docs usability — todo

Plan: /Users/kyle.taylor/.claude/plans/moonlit-weaving-hammock.md

## Phase 1 — AI Guard: Python (config-only)
- [x] Check ddtrace pin — already >=4.7.1, no bump needed (DD_ENV/DD_SERVICE already present too)
- [x] Add DD_TRACE_ENABLED + DD_AI_GUARD_ENABLED to k8s/agent-api/configmap.yaml
- [x] Add DD_API_KEY + DD_APP_KEY to create-agent-api-secret Makefile target + k8s/secrets/agent-api-secret.yaml
- [~] Verify: malicious prompt test against /query (live-deployment verification)

## Phase 2 — AI Guard: .NET (new code)
- [x] New Services/DatadogAiGuardClient.cs (modeled on DatadogEvalsClient.cs)
- [x] Wire into AgentService.RunAgentAsync + RunAgentStreamingAsync (pre-flight guard, block on DENY/ABORT)
- [x] Manual Activity span tagging for partial trace visibility (ai_guard.action/reason/duration_ms)
- [x] New AiGuardSubmissionLog.cs ring buffer
- [x] New GET /ai-guard/status endpoint
- [x] New AiGuardDiagnostics.tsx admin panel, wired into AdminTab.tsx
- [x] Add DD_APPLICATION_KEY to create-agent-api-dotnet-secret Makefile target + k8s/secrets/agent-api-dotnet-secret.yaml (envFrom secretRef — no per-key deployment.yaml change needed)
- [x] dotnet build -c Release: 0 warnings, 0 errors
- [x] npx tsc --noEmit (ui): 0 errors
- [~] Verify: malicious prompt test blocks on /query and /query/stream; /ai-guard/status shows submission; admin panel renders; APM span visible (live-deployment verification)

## Phase 3 — Parity doc + small Python catch-up
- [x] Rewrite docs/development/dotnet-python-parity.mdx feature matrix with current findings
- [x] Move Tier 2-4 items to explicit backlog section, add "shipped this round" AI Guard note
- [x] Curated _SEED_POOL (12 entries, mirrors .NET) + _TOOL_CATALOG constant in services/agent-api/src/main.py
- [~] uv run pytest (running in background, awaiting result)

## Phase 4 — Docs usability
- [x] Standardize roadmap/aspirational <Aside> callout — CORRECTED FINDING: the research agent's grep OR'd three patterns together; the 6 "other" pages' <Aside type="caution"> blocks are ordinary operational warnings, not roadmap/aspirational framing. Only experiments.mdx documents unshipped work, and it already does so correctly/uniquely. No inconsistency to fix — skipped.
- [x] Add check-links script to docs/package.json (docs/scripts/check-links.js, new)
- [x] New docs/llm-engineering/ai-guard.mdx page + sidebar entry in astro.config.mjs
- [x] npx astro build: 59 pages, 0 errors
- [x] npm run check-links: 0 broken internal links across 59 pages

## Review

**Phase 1 (Python AI Guard):** Config-only as planned. ddtrace already >=4.7.1 (no bump needed). Added DD_TRACE_ENABLED/DD_AI_GUARD_ENABLED to configmap, DD_API_KEY/DD_APP_KEY to the secret template + Makefile target (WARN-not-ERROR, consistent with how DD_API_KEY is optional elsewhere). No code changes — this is the whole point of the LangChain auto-integration.

**Phase 2 (.NET AI Guard):** New DatadogAiGuardClient.cs + AiGuardSubmissionLog.cs, wired into both RunAgentAsync and RunAgentStreamingAsync as a pre-flight check before the agent loop runs. Fails open on any transport error (never blocks legitimate traffic on a DD outage). Added GET /ai-guard/status + AiGuardDiagnostics.tsx admin panel since DD's HTTP API sends no native traces — manual Activity span tagging is the substitute. dotnet build and tsc --noEmit both clean.

**Phase 3 (Parity doc):** Rewrote the stale 2026-05-16 matrix — tool descriptions and suggestion-pool mechanism were incorrectly marked ❌ (now ✅/◐), the real gap (external evals pipeline) is now flagged as the largest remaining item, and Tier 2-4 moved into an explicit backlog section. Did the optional Python catch-up too: curated 12-entry _SEED_POOL + structured _TOOL_CATALOG in main.py, mirroring .NET's SuggestionService. 23/23 pytest passed.

**Phase 4 (Docs usability):** One planned task turned out to be based on a research false-positive — the "6 pages mix shipped/aspirational content" finding came from an OR'd grep pattern that matched ordinary `<Aside type="caution">` operational warnings, not roadmap framing. Only experiments.mdx actually documents unshipped work, and it already does so correctly. Skipped that non-fix rather than force a change. Shipped the two real gaps instead: a net-new check-links script (nothing like it existed) and the new ai-guard.mdx page.

**Lesson for next time:** when a subagent's grep uses `\|` (OR) across dissimilar patterns, the match list conflates unrelated hits — worth spot-checking a sample of "found N files matching X" claims against the actual files before acting on them, especially when the finding drives a multi-file edit.
