import { datadogRum } from "@datadog/browser-rum";

export function initDatadogRum(): void {
  const appId = import.meta.env.VITE_DD_RUM_APP_ID;
  const clientToken = import.meta.env.VITE_DD_RUM_CLIENT_TOKEN;

  if (!appId || !clientToken) {
    console.warn("Datadog RUM: VITE_DD_RUM_APP_ID or VITE_DD_RUM_CLIENT_TOKEN not set — RUM disabled");
    return;
  }

  datadogRum.init({
    applicationId: appId,
    clientToken: clientToken,
    site: (import.meta.env.VITE_DD_RUM_SITE as string) || "us3.datadoghq.com",
    service: "infra-advisor-ui",
    env: (import.meta.env.VITE_DD_ENV as string) || "dev",
    version: "1.0.0",
    sessionSampleRate: 100,
    sessionReplaySampleRate: 100,
    trackUserInteractions: true,
    trackResources: true,
    trackLongTasks: true,
    defaultPrivacyLevel: "mask-user-input",
    // Inject Datadog trace headers so RUM sessions correlate with backend APM spans
    allowedTracingUrls: [
      { match: /\/api\//i, propagatorTypes: ["datadog"] as const },
      ...((import.meta.env.VITE_AGENT_API_URL as string | undefined) &&
      import.meta.env.VITE_AGENT_API_URL !== "/api"
        ? [{ match: import.meta.env.VITE_AGENT_API_URL as string, propagatorTypes: ["datadog"] as const }]
        : []),
    ],
  });

  datadogRum.startSessionReplayRecording();
}

// ─── Custom RUM actions ─────────────────────────────────────────────────────

export function trackQuerySubmitted(queryLength: number, domain?: string): void {
  datadogRum.addAction("query_submitted", {
    query_length: queryLength,
    domain: domain ?? "unknown",
  });
}

export function trackSuggestionClicked(suggestion: string): void {
  datadogRum.addAction("suggestion_clicked", {
    suggestion_preview: suggestion.slice(0, 80),
  });
}

export function trackCitationExpanded(documentType: string, score?: number): void {
  datadogRum.addAction("citation_expanded", {
    document_type: documentType,
    relevance_score: score,
  });
}

export function trackBridgeCardRendered(count: number): void {
  datadogRum.addAction("bridge_card_rendered", {
    card_count: count,
  });
}
