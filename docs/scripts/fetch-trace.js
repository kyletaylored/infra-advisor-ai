#!/usr/bin/env node
/**
 * fetch-trace.js — fetch a Datadog distributed trace and save it to src/data/
 *
 * Usage:
 *   npm run fetch-trace -- <trace_id> [output_filename]
 *
 * Examples:
 *   npm run fetch-trace -- 1234567890abcdef
 *   npm run fetch-trace -- 1234567890abcdef my-trace.json
 *
 * Environment variables (or a docs/.env file):
 *   DD_API_KEY   – Datadog API key
 *   DD_APP_KEY   – Datadog Application key
 *   DD_SITE      – Datadog site  (default: datadoghq.com)
 *
 * Output shape (one object, saved to src/data/<filename>):
 *   {
 *     "traceId": "<string>",
 *     "fetchedAt": "<ISO timestamp>",
 *     "spans": [ { service, name, resource, spanID, parentID, duration, type, error? }, … ]
 *   }
 */

import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const DOCS_ROOT = resolve(__dirname, "..");
const DATA_DIR = resolve(DOCS_ROOT, "src", "data");

// ─── env / .env loading ───────────────────────────────────────────────────────

function loadEnv() {
  const envPath = resolve(DOCS_ROOT, ".env");
  try {
    const lines = readFileSync(envPath, "utf8").split("\n");
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#")) continue;
      const idx = trimmed.indexOf("=");
      if (idx === -1) continue;
      const key = trimmed.slice(0, idx).trim();
      const val = trimmed
        .slice(idx + 1)
        .trim()
        .replace(/^["']|["']$/g, "");
      if (!(key in process.env)) process.env[key] = val;
    }
  } catch {
    // .env is optional — rely on real env vars
  }
}

// ─── BigInt safety ────────────────────────────────────────────────────────────
// Datadog trace IDs, span IDs, and parent IDs can exceed Number.MAX_SAFE_INTEGER
// (9007199254740991). We quote them as strings before JSON.parse so precision
// is preserved throughout.

function safeParseJson(text) {
  const quoted = text.replace(
    /"(spanID|parentID|traceID|traceIDFull)":\s*(\d+)/g,
    (_, key, val) => `"${key}":"${val}"`,
  );
  return JSON.parse(quoted);
}

// ─── PII field stripping ──────────────────────────────────────────────────────
// Keep only the fields the visualiser actually needs.

const KEEP_FIELDS = {
  service: 'service',
  name: 'name',
  resource: 'resource',
  spanID: 'spanID',
  parentID: 'parentID',
  duration: 'duration',
  type: 'type',
  error: 'error',
  'meta.gen_ai.operation.name': 'gen_ai_operation',
};

function getPath(obj, path) {
  if (obj == null) return undefined;

  // Exact key match first
  if (path in obj) return obj[path];

  const parts = path.split('.');

  for (let i = 1; i < parts.length; i++) {
    const head = parts.slice(0, i).join('.');
    const tail = parts.slice(i).join('.');

    if (head in obj) {
      return getPath(obj[head], tail);
    }
  }

  return undefined;
}

function sanitiseSpan(s) {
  const out = {};

  for (const [sourcePath, outputKey] of Object.entries(KEEP_FIELDS)) {
    const value = getPath(s, sourcePath);
    if (value !== undefined) {
      out[outputKey] = value;
    }
  }

  if (!('parentID' in out)) out.parentID = '0';
  if (out.error !== 1) delete out.error;
  if (out?.gen_ai_operation !== undefined) out.type = 'llm';
  console.log(out);
  return out;
}

// ─── main ─────────────────────────────────────────────────────────────────────

async function main() {
  loadEnv();

  const traceId = process.argv[2];
  if (!traceId) {
    console.error("Usage: npm run fetch-trace -- <trace_id> [output_filename]");
    process.exit(1);
  }

  const apiKey = process.env.DD_API_KEY;
  const appKey = process.env.DD_APP_KEY;
  const site = process.env.DD_SITE ?? "datadoghq.com";

  if (!apiKey || !appKey) {
    console.error(
      "Error: DD_API_KEY and DD_APP_KEY must be set (env vars or docs/.env).",
    );
    process.exit(1);
  }

  const outputFile = process.argv[3] ?? `trace-${traceId.slice(0, 16)}.json`;
  const outputPath = resolve(DATA_DIR, outputFile);

  const url = `https://api.${site}/api/v2/trace/${traceId}`;
  console.log(`Fetching: ${url}`);

  const res = await fetch(url, {
    headers: {
      "DD-API-KEY": apiKey,
      "DD-APPLICATION-KEY": appKey,
      Accept: "application/json",
    },
  });

  if (!res.ok) {
    const body = await res.text().catch(() => "");
    console.error(`HTTP ${res.status}: ${res.statusText}\n${body}`);
    process.exit(1);
  }

  const raw = await res.text();
  const payload = safeParseJson(raw);

  // The v2 trace endpoint returns:
  //   { data: { id, type, attributes: { spans: [...] } } }
  const spans = payload?.data?.attributes?.spans;
  if (!Array.isArray(spans)) {
    console.error(
      "Unexpected response shape — no data.attributes.spans array.",
    );
    console.error(JSON.stringify(payload, null, 2).slice(0, 500));
    process.exit(1);
  }

  const sanitised = spans.map(sanitiseSpan);

  const output = {
    traceId,
    fetchedAt: new Date().toISOString(),
    spans: sanitised,
  };

  mkdirSync(DATA_DIR, { recursive: true });
  writeFileSync(outputPath, JSON.stringify(output, null, 2) + "\n");

  console.log(`Saved ${sanitised.length} spans → ${outputPath}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
