import { datadogRum } from "@datadog/browser-rum";

type PropagatorType = "datadog" | "b3" | "b3multi" | "tracecontext";

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
    version: (import.meta.env.VITE_APP_VERSION as string) || "local",
    sessionSampleRate: 100,
    sessionReplaySampleRate: 100,
    trackBfcacheViews: true,
    defaultPrivacyLevel: "mask-user-input",
    // Trace all same-origin requests so /api/, /auth/, and any future endpoints correlate with APM
    allowedTracingUrls: [
      { match: window.location.origin, propagatorTypes: ["datadog" as PropagatorType] },
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

export function trackMessageFeedback(positive: boolean, domain?: string): void {
  datadogRum.addAction("message_feedback", {
    feedback: positive ? "positive" : "negative",
    domain: domain ?? "unknown",
  });
}

export function trackMessageCopied(domain?: string): void {
  datadogRum.addAction("message_copied", {
    domain: domain ?? "unknown",
  });
}

export function trackMessageReported(domain?: string): void {
  datadogRum.addAction("message_reported", {
    domain: domain ?? "unknown",
  });
}
