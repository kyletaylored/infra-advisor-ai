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
    site: "datadoghq.com",
    service: "infra-advisor-ui",
    env: import.meta.env.VITE_DD_ENV || "dev",
    version: "1.0.0",
    sessionSampleRate: 100,
    sessionReplaySampleRate: 100,
    trackUserInteractions: true,
    trackResources: true,
    trackLongTasks: true,
    defaultPrivacyLevel: "mask-user-input", // mask text inputs for PII
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
