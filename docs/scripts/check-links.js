#!/usr/bin/env node
/**
 * check-links.js — crawl the built site for internal links that 404.
 *
 * Usage:
 *   npm run build && npm run check-links
 *
 * Walks dist/**\/*.html, extracts every <a href="..."> pointing under the
 * site's base path (astro.config.mjs `base: '/infra-advisor-ai'`), and
 * verifies each one resolves to a file that actually exists in dist/.
 * External links, mailto:, and same-page anchors (#foo) are skipped —
 * this only catches broken internal navigation, which is what silently
 * rots as pages get renamed/moved/deleted.
 *
 * Exits 1 (and prints every broken link with the page it was found on)
 * if anything is broken; exits 0 otherwise.
 */

import { readFileSync, readdirSync, existsSync } from "node:fs";
import { resolve, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const DOCS_ROOT = resolve(__dirname, "..");
const DIST_DIR = join(DOCS_ROOT, "dist");
const BASE = "/infra-advisor-ai";

if (!existsSync(DIST_DIR)) {
  console.error(`dist/ not found at ${DIST_DIR} — run "npm run build" first.`);
  process.exit(1);
}

function listHtmlFiles(dir, out = []) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) listHtmlFiles(full, out);
    else if (entry.name.endsWith(".html")) out.push(full);
  }
  return out;
}

const htmlFiles = listHtmlFiles(DIST_DIR);
const HREF_RE = /<a\s+[^>]*href="([^"]+)"/g;

// A dist/<slug>/index.html page resolves both "/base/slug" and
// "/base/slug/" — Astro/Starlight always emits the trailing-slash form,
// but authored links sometimes omit it.
function resolveInternalPath(href) {
  let path = href.split("#")[0].split("?")[0];
  if (!path.startsWith(BASE)) return null; // not an internal link
  path = path.slice(BASE.length) || "/";
  if (!path.endsWith("/")) path += "/";
  return join(DIST_DIR, path, "index.html");
}

let brokenCount = 0;
for (const file of htmlFiles) {
  const html = readFileSync(file, "utf8");
  const seen = new Set();
  for (const match of html.matchAll(HREF_RE)) {
    const href = match[1];
    if (seen.has(href)) continue;
    seen.add(href);

    if (
      href.startsWith("http://") || href.startsWith("https://") ||
      href.startsWith("mailto:") || href.startsWith("#") ||
      !href.startsWith(BASE)
    ) {
      continue; // external, anchor, or outside the site's base path
    }

    const target = resolveInternalPath(href);
    if (target && !existsSync(target)) {
      const relSource = file.slice(DIST_DIR.length);
      console.error(`BROKEN LINK: ${href}\n  found in: dist${relSource}`);
      brokenCount++;
    }
  }
}

if (brokenCount > 0) {
  console.error(`\n${brokenCount} broken internal link(s) found.`);
  process.exit(1);
}

console.log(`check-links: 0 broken internal links across ${htmlFiles.length} pages.`);
