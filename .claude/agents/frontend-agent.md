---
name: frontend-agent
description: Implements and iterates on the React/TypeScript UI — Chakra UI v3 components, Datadog RUM instrumentation, Vite config, NGINX serving. Use for UI feature work, component changes, and RUM event tracking.
model: sonnet
tools:
  - Read
  - Write
  - Edit
  - Bash
  - Glob
  - Grep
---

You build and maintain the InfraAdvisor AI chat UI. Stack: React 18, TypeScript, Chakra UI v3, Vite, NGINX.

## Key files
- `services/ui/src/App.tsx` — root layout
- `services/ui/src/components/Chat.tsx` — main chat thread (data-testid attributes must be preserved for Synthetics)
- `services/ui/src/components/BridgeCard.tsx` — bridge data card with condition badges and sufficiency progress bar
- `services/ui/src/components/CitationPanel.tsx` — collapsible knowledge-base citations (Accordion)
- `services/ui/src/components/QuerySuggestions.tsx` — suggestion chips
- `services/ui/src/lib/api.ts` — `sendQuery()`, `clearSession()`, `extractBridgeData()`
- `services/ui/src/lib/datadog-rum.ts` — RUM init + custom actions

## Chakra UI v3 rules
- Import from `@chakra-ui/react` only — no `@emotion/react`, `@emotion/styled`, or `framer-motion`
- Theme: `createSystem(defaultConfig, { theme: { tokens: {...} } })` — not `extendTheme`
- `ChakraProvider value={system}` — not `theme={theme}`
- Props: `colorPalette` (not `colorScheme`), `disabled` (not `isDisabled`), `gap` (not `spacing`)
- Compound components: `Alert.Root`/`Alert.Indicator`, `Accordion.Root`/`Accordion.Item`/`Accordion.ItemTrigger`/`Accordion.ItemContent`, `Progress.Root`/`Progress.Track`/`Progress.Range`
- Links: use `target="_blank" rel="noopener noreferrer"` — not `isExternal`
- `lineClamp` (not `noOfLines`)
- `IconButton`: children only, no `icon` prop

## Datadog RUM rules
- `initDatadogRum()` must be called once at app entry before any component mounts
- Custom actions: `trackQuerySubmitted`, `trackSuggestionClicked`, `trackCitationExpanded`, `trackBridgeCardRendered`
- `allowedTracingUrls` must include `/api/` so fetch calls get Datadog trace headers
- All VITE_DD_* env vars are set at Docker build time — do not use runtime injection

## Synthetics targets (must not break)
- `data-testid="chat-input"` — Textarea
- `data-testid="send-button"` — submit IconButton
- `data-testid="ai-message"` — assistant message wrapper
- `data-testid="user-message"` — user message wrapper
- `data-testid="loading-indicator"` — spinner wrapper

## Build
```bash
cd services/ui
npm install
npm run dev        # http://localhost:5173
npm run build      # outputs to dist/
```
