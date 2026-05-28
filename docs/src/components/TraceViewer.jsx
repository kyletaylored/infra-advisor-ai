/**
 * TraceViewer.jsx — distributed trace visualizer for Astro Starlight
 *
 * Install: npm install @xyflow/react
 *
 * Usage in an .mdx page:
 *   import TraceViewer from '@components/TraceViewer.jsx';
 *   import traceData  from '@data/sample-trace.json';
 *   <TraceViewer client:load spans={traceData.spans} />
 *
 * Props:
 *   spans   – Array  – the `data` array from a Datadog Spans API response
 *   height  – number – canvas height in px (default 480)
 */

import React, { useCallback } from 'react';
import {
  ReactFlow,
  Handle,
  Position,
  MarkerType,
  Background,
  Controls,
  useNodesState,
  useEdgesState,
  addEdge,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

// ─── CSS reset + edge visibility ──────────────────────────────────────────────
// Two separate problems, both caused by Starlight's global CSS leaking into the
// ReactFlow SVG/DOM:
//
// 1. margin-top bleed: .sl-markdown-content adds margin-top to all block
//    descendants. We zero it out scoped to our node wrappers.
//
// 2. Invisible edges: Starlight (or the Astro base reset) sets `fill: none` and
//    possibly `stroke: none` on SVG elements globally. ReactFlow's edge <path>
//    elements rely on the inline `stroke` from the edge's `style` prop, but a
//    global `stroke: none !important` or `fill: currentColor` override makes
//    them invisible. We force the values explicitly inside `.react-flow`.
//
// Theme variables mirror the xy horizontal-flow demo so hover/selection states
// look right without importing the full theme CSS.
const STYLE_RESET = `
  /* ── xy-flow theme variables (scoped to our widget) ── */
  .react-flow {
    --xy-theme-edge-hover:    #334155;
    --xy-theme-hover:         #c5c5c5;
    --xy-theme-selected:      #6366f1;
    --xy-theme-color-focus:   #e8e8e8;
    --xy-handle-background-color-default: #ffffff;
    --xy-handle-border-color-default:     #aaaaaa;
  }

  /* ── node wrapper chrome ── */
  .react-flow__node-span {
    padding:    0 !important;
    border:     none !important;
    background: transparent !important;
    box-shadow: none !important;
  }

  /* ── Starlight margin-top bleed ── */
  .react-flow__node-span *,
  .react-flow__node-span *::before,
  .react-flow__node-span *::after {
    margin-top:    0 !important;
    margin-bottom: 0 !important;
    box-sizing:    border-box !important;
  }

  /* ── Edge path visibility ──────────────────────────────────────────────────
     Starlight's global CSS can set stroke/fill on SVG elements to values that
     hide ReactFlow edges. We force the edge paths to be visible. The colour
     here is the fallback; each edge still carries its own inline stroke via
     the style prop, but !important on stroke-opacity ensures it isn't zeroed.
  */
  .react-flow__edge-path {
    stroke:         #94A3B8 !important;
    stroke-width:   1.5px   !important;
    stroke-opacity: 1       !important;
    fill:           none    !important;
  }

  /* Hover / selected edge colour */
  .react-flow__edge.selectable:hover .react-flow__edge-path,
  .react-flow__edge.selectable.selected .react-flow__edge-path {
    stroke: var(--xy-theme-edge-hover) !important;
  }

  /* Animated dash — the moving dot on animated:true edges */
  .react-flow__edge-path.animated {
    stroke-dasharray:  5 5;
    animation:         rf-dashdraw 0.5s linear infinite;
  }
  @keyframes rf-dashdraw {
    from { stroke-dashoffset: 10; }
    to   { stroke-dashoffset:  0; }
  }

  /* Arrow marker fill must match the stroke or it disappears */
  .react-flow__arrowhead path {
    fill:         #94A3B8 !important;
    stroke:       none    !important;
    fill-opacity: 1       !important;
  }
  .react-flow__edge.selectable:hover .react-flow__arrowhead path,
  .react-flow__edge.selectable.selected .react-flow__arrowhead path {
    fill: var(--xy-theme-edge-hover) !important;
  }

  /* Handle dots */
  .react-flow__handle {
    background-color: var(--xy-handle-background-color-default) !important;
    border-color:     var(--xy-handle-border-color-default)     !important;
  }
  .react-flow__handle.connectionindicator:hover {
    border-color:     var(--xy-theme-edge-hover) !important;
    background-color: #ffffff                    !important;
  }
`;

// ─── layout constants ─────────────────────────────────────────────────────────

const NODE_W = 160;  // card width  (px) — also written to node.width for edge anchoring
const NODE_H = 86;   // card height (px) — approximate; written to node.height for edge anchoring
const GAP_X = 72;   // horizontal gap between depth ranks
const GAP_Y = 24;   // vertical gap between sibling nodes

// ─── palettes ─────────────────────────────────────────────────────────────────

const TYPE_PALETTE = {
  browser:  { bg: '#EFF6FF', border: '#3B82F6', badge: '#2563EB', text: '#1D4ED8' },
  web:      { bg: '#F5F3FF', border: '#8B5CF6', badge: '#7C3AED', text: '#5B21B6' },
  sql:      { bg: '#FFF7ED', border: '#F97316', badge: '#EA580C', text: '#C2410C' },
  redis:    { bg: '#FFF1F2', border: '#F43F5E', badge: '#E11D48', text: '#BE123C' },
  http:     { bg: '#F0FDFA', border: '#14B8A6', badge: '#0D9488', text: '#0F766E' },
  custom:   { bg: '#F0FDF4', border: '#22C55E', badge: '#16A34A', text: '#15803D' },
  cache:    { bg: '#FFF1F2', border: '#F43F5E', badge: '#E11D48', text: '#BE123C' },
  grpc:     { bg: '#F0FDFA', border: '#14B8A6', badge: '#0D9488', text: '#0F766E' },
  mongodb:  { bg: '#F0FDF4', border: '#22C55E', badge: '#16A34A', text: '#15803D' },
  graphql:  { bg: '#FDF4FF', border: '#C026D3', badge: '#A21CAF', text: '#86198F' },
};
const FALLBACK = { bg: '#F9FAFB', border: '#9CA3AF', badge: '#6B7280', text: '#374151' };

const STATUS_COLOR = {
  ok: '#22C55E',
  error: '#EF4444',
  warn: '#F59E0B',
};

// ─── helpers ──────────────────────────────────────────────────────────────────

function fmtDuration(ns) {
  if (!ns) return '—';
  const ms = ns / 1e6;
  if (ms < 1) return `${(ns / 1e3).toFixed(0)} µs`;
  if (ms < 1000) return `${ms.toFixed(1)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

// ─── SpanNode ─────────────────────────────────────────────────────────────────
// data shape: { operation, resource, service, type, status, duration, isRoot, isLeaf, spanId }
//
// Handles are conditional:
//   isRoot → no target handle (nothing points into it)
//   isLeaf → no source handle (nothing comes out of it)

function SpanNode({ data }) {
  const p = TYPE_PALETTE[data.type] ?? FALLBACK;
  const dot = STATUS_COLOR[data.status] ?? '#9CA3AF';
  const showResource = data.resource && data.resource !== data.operation;

  // Handles must be SIBLINGS of the card div (fragment pattern), not children.
  // When nested inside a position:relative div, ReactFlow registers their
  // positions relative to that div's origin rather than the node wrapper, which
  // breaks edge routing. Fragment siblings are positioned relative to the node
  // wrapper (.react-flow__node-span) directly, which is what ReactFlow expects.
  return (
    <>
      {/* left (target) handle — omitted for root spans */}
      {!data.isRoot && <Handle type="target" position={Position.Left} />}

      <div
        className={`trace-span-card trace-span-type-${data.type || 'unknown'} trace-span-status-${data.status || 'unknown'}`}
        data-span-id={data.spanId}
        data-service={data.service}
        data-type={data.type}
        data-status={data.status}
        style={{
          width: NODE_W,
          background: p.bg,
          border: `1.5px solid ${p.border}`,
          borderRadius: 6,
          padding: '6px 9px 5px',
          fontFamily: 'ui-sans-serif, system-ui, sans-serif',
          fontSize: 11,
          boxShadow: '0 1px 4px rgba(0,0,0,0.08)',
          boxSizing: 'border-box',
        }}
      >
        {/* badge + status row */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '3px' }}>
          <span style={{
            background: p.badge, color: '#fff', borderRadius: 3,
            padding: '1px 4px', fontSize: 8, fontWeight: 700,
            textTransform: 'uppercase', letterSpacing: '0.05em',
            lineHeight: 1.5,
          }}>
            {data.type || 'span'}
          </span>
          <span style={{ display: 'flex', alignItems: 'center', gap: '3px', fontSize: 9, color: dot, fontWeight: 600, lineHeight: 1 }}>
            <span style={{ width: 5, height: 5, borderRadius: '50%', background: dot, display: 'inline-block', flexShrink: 0 }} />
            {data.status}
          </span>
        </div>

        {/* service */}
        <div style={{
          color: '#94A3B8', fontSize: 9, marginBottom: '2px',
          overflow: 'hidden', whiteSpace: 'nowrap', textOverflow: 'ellipsis',
          lineHeight: 1.3,
        }}>
          {data.service}
        </div>

        {/* operation */}
        <div style={{
          color: p.text, fontWeight: 700, fontSize: 11, lineHeight: 1.2,
          marginBottom: showResource ? '2px' : '5px',
          overflow: 'hidden', whiteSpace: 'nowrap', textOverflow: 'ellipsis',
        }}>
          {data.operation}
        </div>

        {/* resource (only when different from operation) */}
        {showResource && (
          <div style={{
            color: '#64748B', fontSize: 9.5, lineHeight: 1.2, marginBottom: '5px',
            overflow: 'hidden', whiteSpace: 'nowrap', textOverflow: 'ellipsis',
          }}>
            {data.resource}
          </div>
        )}

        {/* duration */}
        <div style={{
          borderTop: `1px solid ${p.border}40`, paddingTop: '3px',
          textAlign: 'right', color: p.text, fontSize: 9.5, fontWeight: 700, lineHeight: 1,
        }}>
          {data.duration}
        </div>
      </div>

      {/* right (source) handle — omitted for leaf spans */}
      {!data.isLeaf && <Handle type="source" position={Position.Right} />}
    </>
  );
}

const NODE_TYPES = { span: SpanNode };

// ─── schema normalisation ─────────────────────────────────────────────────────
//
// Two supported input shapes:
//
//   NEW (v2 trace endpoint, fetch-trace.js output):
//     spans prop is the `spans` array from the saved JSON file:
//       [ { service, name, resource, spanID, parentID, duration, type, error? }, … ]
//     IDs are strings (BigInt-safe).
//
//   OLD (v2 spans/events endpoint):
//     spans prop is the `data` array from the API:
//       [ { id, attributes: { span_id, parent_id, operation_name, resource_name,
//           service, type, status, custom: { duration } } }, … ]
//
// Detection: new format has a `spanID` string property on the first element;
// old format has an `attributes` object.

function normaliseSpan(s) {
  if (s.spanID !== undefined) {
    // ── new v2 trace format ──
    return {
      nodeId:    String(s.spanID),
      spanId:    String(s.spanID),
      parentId:  s.parentID != null ? String(s.parentID) : '0',
      operation: s.name     ?? '—',
      resource:  s.resource ?? '',
      service:   s.service  ?? '—',
      type:      s.type     ?? '',
      status:    s.error === 1 ? 'error' : 'ok',
      duration:  fmtDuration(s.duration),
    };
  }

  // ── old spans/events format ──
  const a = s.attributes ?? {};
  return {
    nodeId:    s.id,
    spanId:    a.span_id   ?? s.id,
    parentId:  a.parent_id ?? '0',
    operation: a.operation_name ?? '—',
    resource:  a.resource_name  ?? '',
    service:   a.service  ?? '—',
    type:      a.type     ?? '',
    status:    a.status   ?? '',
    duration:  fmtDuration(a.custom?.duration),
  };
}

// ─── tree layout ──────────────────────────────────────────────────────────────

function buildLayout(spans) {
  if (!Array.isArray(spans) || spans.length === 0) return { nodes: [], edges: [] };

  // normalise to a uniform shape and deduplicate by nodeId (keep first occurrence)
  const seen   = new Set();
  const unique = spans
    .map(normaliseSpan)
    .filter(s => {
      if (!s.nodeId || seen.has(s.nodeId)) return false;
      seen.add(s.nodeId);
      return true;
    });

  // nodeId string → index in `unique` (fast lookup for parent resolution)
  const idSet = new Set(unique.map(s => s.nodeId));

  // build edges — skip if parent is '0' / '0000…' or not present in the span list
  const edges = unique.flatMap(s => {
    const parentIsRoot = !s.parentId || s.parentId === '0' || /^0+$/.test(s.parentId);
    if (parentIsRoot || !idSet.has(s.parentId)) return [];
    return [{
      id: `${s.parentId}→${s.nodeId}`,
      source: s.parentId,
      target: s.nodeId,
      // No sourceHandle/targetHandle — each node has at most one of each,
      // so ReactFlow resolves them automatically. Explicit IDs can silently
      // fail if the store hasn't resolved handles by the time edges are drawn.
      type: 'smoothstep',
      animated: true,
      markerEnd: { type: MarkerType.ArrowClosed, width: 10, height: 10, color: '#94A3B8' },
      style: { stroke: '#94A3B8', strokeWidth: 1.5 },
    }];
  });

  // build adjacency map for layout + root/leaf detection
  const children = Object.fromEntries(unique.map(s => [s.nodeId, []]));
  const hasParent = new Set();
  edges.forEach(e => { children[e.source]?.push(e.target); hasParent.add(e.target); });

  const rootIds = new Set(unique.map(s => s.nodeId).filter(id => !hasParent.has(id)));
  const leafIds = new Set(unique.map(s => s.nodeId).filter(id => (children[id] ?? []).length === 0));

  // two-pass tree layout — post-order assigns leaf y-slots, pre-order centres parents
  const pos = {};
  let slot = 0;
  function place(id, depth) {
    const ch = children[id] ?? [];
    if (ch.length === 0) {
      pos[id] = { x: depth * (NODE_W + GAP_X), y: slot * (NODE_H + GAP_Y) };
      slot++;
    } else {
      ch.forEach(c => place(c, depth + 1));
      const ys = ch.map(c => pos[c].y);
      pos[id] = { x: depth * (NODE_W + GAP_X), y: (Math.min(...ys) + Math.max(...ys)) / 2 };
    }
  }
  [...rootIds].forEach(r => place(r, 0));

  // build nodes — width + height are required for ReactFlow to anchor edges
  // before the DOM has been measured (avoids "no edges on first render" bug)
  const nodes = unique.map(s => ({
    id:              s.nodeId,
    type:            'span',
    className:       `trace-node trace-node-type-${s.type || 'unknown'}`,
    sourcePosition:  Position.Right,
    targetPosition:  Position.Left,
    position:        pos[s.nodeId] ?? { x: 0, y: 0 },
    width:           NODE_W,   // ← required for edge anchoring before DOM measurement
    height:          NODE_H,   // ← required for edge anchoring before DOM measurement
    data: {
      spanId:    s.spanId,
      operation: s.operation,
      resource:  s.resource,
      service:   s.service,
      type:      s.type,
      status:    s.status,
      duration:  s.duration,
      isRoot:    rootIds.has(s.nodeId),
      isLeaf:    leafIds.has(s.nodeId),
    },
  }));

  return { nodes, edges };
}

// ─── component ────────────────────────────────────────────────────────────────

export default function TraceViewer({ spans = [], height = 480 }) {
  const { nodes: init, edges: initEdges } = buildLayout(spans);

  const [nodes, , onNodesChange] = useNodesState(init);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initEdges);

  const onConnect = useCallback(
    (params) => setEdges((es) => addEdge(params, es)),
    [setEdges],
  );

  if (init.length === 0) {
    return (
      <div style={{
        height, display: 'flex', alignItems: 'center', justifyContent: 'center',
        border: '1px dashed #CBD5E1', borderRadius: 8,
        color: '#94A3B8', fontFamily: 'ui-sans-serif, system-ui, sans-serif', fontSize: 13,
      }}>
        No trace data provided.
      </div>
    );
  }

  return (
    // `not-content` is Starlight's escape hatch — it opts every descendant out
    // of .sl-markdown-content's block-element resets (including `height: auto`
    // on <svg>, which collapses ReactFlow's zero-intrinsic-height edge SVG to
    // 0px and makes edges invisible).
    <div className="not-content" style={{ width: '100%', height, borderRadius: 8, overflow: 'hidden', border: '1px solid #E2E8F0' }}>
      <style>{STYLE_RESET}</style>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        nodeTypes={NODE_TYPES}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        attributionPosition="bottom-left"
      >
        <Background />
        <Controls />
      </ReactFlow>
    </div>
  );
}
