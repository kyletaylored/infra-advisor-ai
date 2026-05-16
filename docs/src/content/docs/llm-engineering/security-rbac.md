---
title: Data security & RBAC
description: Sensitive Data Scanner, role-based access control to LLMObs spans, prompt sanitization, and the practical defaults to start with.
sidebar:
  order: 6
  label: Data security & RBAC
---

import { Aside } from '@astrojs/starlight/components';

LLM Observability captures prompts, completions, tool inputs, and tool outputs. That's enormously valuable for debugging — and enormously sensitive. Three concerns to address before going to real users:

1. **Sensitive data in spans.** Users paste PII, credentials, or proprietary content. By default it lands in DD verbatim.
2. **Who can read spans.** A new engineer joining shouldn't have read access to every user conversation by default.
3. **Audit trail.** When someone *does* read a span, that access should be logged.

DD provides building blocks for each. This page is about the defaults to set on day one.

## Sensitive Data Scanner

DD's **Sensitive Data Scanner** runs regex/ML-based detection over span content at ingest time and either redacts or annotates. Setup:

1. **DD → Compliance → Sensitive Data Scanner → Create scanning group.**
2. **Scope:** filter to `@ml_app:infra-advisor-*` so only LLMObs spans are scanned.
3. **Rules:** turn on the prebuilt rules for the data types you handle. Common starting set:
   - Email addresses
   - Phone numbers
   - Credit card numbers
   - API keys / secrets (DD has a library of cloud provider key formats)
   - Custom: any project-specific PII you can express as a regex
4. **Action:** redact by default. Tag the span with `@sds.detected:email` (or whichever rule fired) for forensics.

Redaction happens before the span is queryable. The original content is **not retained** anywhere in DD after redaction. Plan for that — you can't "un-redact" later.

## RBAC on LLMObs spans

DD's role system covers LLMObs:

- **`llm_obs_read`** permission gates access to the LLMObs UI and API endpoints for spans.
- **`llm_obs_write`** gates the ability to create evaluators, annotation queues, datasets.
- Both are assignable to standard DD roles.

Recommended default:

| Role | LLMObs perms |
|---|---|
| `Datadog Read Only` | none |
| `Datadog Standard` | none |
| Custom role `LLM Engineer` | `llm_obs_read`, `llm_obs_write` |
| Custom role `LLM Reviewer` | `llm_obs_read` only |

Make access opt-in. Most engineers don't need to read prompt content from production traces; those who do should be on the explicit list.

## Audit trail

DD's audit log records role assignments and configuration changes. For *content* access — who viewed which span — DD's audit coverage is more limited. Two ways to cover the gap:

1. **Restrict who has `llm_obs_read` to a small, named group.** Easier than auditing reads is keeping the readable population small.
2. **Use the `dd_app_key` audit trail.** Every API-key-driven export shows up in the audit log. Keep export keys scoped per workload so you can attribute pulls.

## Prompt sanitization at the app boundary

Defense-in-depth: don't rely on DD's scanner alone. Sanitize at the app boundary too, before the prompt ever leaves your service.

A few patterns:

- **Strip obvious secrets** before constructing the LLM prompt. Use a library like `detect-secrets` (Python) or `Microsoft.Extensions.AI.Evaluation.Safety` (a complementary .NET option for redaction).
- **Limit history depth.** Long histories are more likely to contain something sensitive someone pasted earlier. Cap to the last N turns.
- **Reject obvious injection attempts** with a managed eval (`Prompt Injection` from DD's catalog) as a gate, not just as a post-hoc score.

<Aside type="caution">
**Don't store secrets in tool outputs.** Tools like `get_credentials_for_user` should return references (e.g., a key vault path), not the secret itself. The tool output ends up in the LLM span and the LLM context — both wider attack surfaces than your service code.
</Aside>

## Quick-start checklist

If you're shipping to real users in the next two weeks:

- [ ] Sensitive Data Scanner enabled with email + phone + credit card rules at minimum.
- [ ] `llm_obs_read` removed from default Standard role.
- [ ] DD app keys for export jobs scoped to a per-job role.
- [ ] Prompt Injection managed evaluator turned on.
- [ ] A short doc telling reviewers what's expected when they DO read spans (no copying content to external systems, etc.).

## What's next

- [Managed evaluations](../evaluations/managed/) — including the Sensitive Data and Prompt Injection evaluators that complement the scanner.
- [Export API](../evaluations/export-api/) — the API key model that needs careful scoping.
