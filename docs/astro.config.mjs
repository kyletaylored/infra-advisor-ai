// @ts-check
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';
import lucode from 'lucode-starlight';

import mdx from '@astrojs/mdx';

export default defineConfig({
    site: 'https://kyletaylored.github.io',
    base: '/infra-advisor-ai',
    integrations: [starlight({
        title: 'InfraAdvisor AI',
        description: 'AI-powered infrastructure advisory platform for AEC/O&M consulting firms',
        logo: {
            src: './src/assets/logo.svg',
            replacesTitle: false,
        },
        favicon: '/favicon.svg',
        customCss: ['./src/styles/brand.css'],
        components: {
            Head: './src/components/Head.astro',
        },
        social: [
            {
                icon: 'github',
                label: 'GitHub',
                href: 'https://github.com/kyletaylored/infra-advisor-ai',
            },
        ],
        lastUpdated: true,
        head: [
            // Browser chrome / mobile address-bar color matches the logo
            // background. Render-blocking-tiny, no JS.
            { tag: 'meta', attrs: { name: 'theme-color', content: '#1d4ed8' } },

            // PNG favicon fallback for browsers that don't render SVG
            // favicons (e.g. older Safari). Starlight's `favicon: ...`
            // option only emits one <link>; add the PNG explicitly here.
            {
                tag: 'link',
                attrs: { rel: 'icon', type: 'image/png', sizes: '32x32', href: '/infra-advisor-ai/favicon.png' },
            },

            // Open Graph + Twitter Card meta — docs links unfurl with the
            // InfraAdvisor banner in Slack, Twitter, GitHub previews, etc.
            { tag: 'meta', attrs: { property: 'og:type', content: 'website' } },
            { tag: 'meta', attrs: { property: 'og:image', content: 'https://kyletaylored.github.io/infra-advisor-ai/og-image.png' } },
            { tag: 'meta', attrs: { property: 'og:image:width', content: '1200' } },
            { tag: 'meta', attrs: { property: 'og:image:height', content: '630' } },
            { tag: 'meta', attrs: { name: 'twitter:card', content: 'summary_large_image' } },
            { tag: 'meta', attrs: { name: 'twitter:image', content: 'https://kyletaylored.github.io/infra-advisor-ai/og-image.png' } },

            {
                // Sidebar scroll behaviour with ClientRouter (SPA navigation):
                //
                // Problem: ClientRouter replaces the full DOM on each navigation,
                // resetting the sidebar's scrollTop to 0. The old scrollSidebarToActive()
                // then scrolled from 0 → active item on every click, which felt jarring.
                //
                // Fix: save scrollTop on astro:before-swap (old DOM still intact),
                // restore it on astro:after-swap (new DOM in place, before paint),
                // then call scrollIntoView({ block:'nearest' }) which only moves the
                // sidebar the minimum amount to reveal the active item — or not at all
                // if it's already visible. On the very first load (DOMContentLoaded)
                // there's no saved position so we just reveal the active item directly.
                tag: 'script',
                content: `
                    (function () {
                        let savedTop = 0;

                        document.addEventListener('astro:before-swap', () => {
                            const c = document.querySelector('.container-sidebar');
                            if (c) savedTop = c.scrollTop;
                        });

                        document.addEventListener('astro:after-swap', () => {
                            const c = document.querySelector('.container-sidebar');
                            if (!c) return;
                            c.scrollTop = savedTop;
                            const a = c.querySelector('a[aria-current="page"]');
                            if (a) a.scrollIntoView({ block: 'nearest', behavior: 'instant' });
                        });

                        document.addEventListener('DOMContentLoaded', () => {
                            const a = document.querySelector('.container-sidebar a[aria-current="page"]');
                            if (a) a.scrollIntoView({ block: 'nearest', behavior: 'instant' });
                        });
                    })();
                `,
            },
            {
                // Mermaid v11 ESM — CDN, renders .mermaid blocks client-side.
                // Reads Starlight's localStorage theme key to match dark/light mode.
                // Hooks into astro:page-load to re-render after client-side navigation.
                tag: 'script',
                attrs: { type: 'module' },
                content: [
                    "import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';",
                    "function t(){const s=localStorage.getItem('starlight-theme');if(s==='dark')return 'dark';if(s==='light')return 'default';return window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'default';}",
                    "mermaid.initialize({startOnLoad:false,theme:t(),securityLevel:'loose'});",
                    "function render(){const n=document.querySelectorAll('.mermaid:not([data-processed])');if(n.length)mermaid.run({nodes:n});}",
                    "render();",
                    "document.addEventListener('astro:page-load',render);",
                ].join('\n'),
            },
        ],
        plugins: [
            lucode({
                navLinks: [
                    { label: 'Architecture', link: '/architecture/' },
                    { label: 'Services', link: '/services/' },
                    { label: 'Observability', link: '/observability/' },
                    { label: 'Deployment', link: '/deployment/' },
                    {
                        label: 'GitHub',
                        link: 'https://github.com/kyletaylored/infra-advisor-ai',
                        attrs: { target: '_blank', rel: 'noopener noreferrer' },
                    },
                ],
                footerText: 'Built with [Lucode Starlight](https://github.com/lucas-labs/lucode-starlight-theme). Powered by Datadog & Azure.',
            }),
        ],
        sidebar: [
            { label: 'Home', link: '/' },
            {
                label: 'Architecture',
                items: [
                    { label: 'Overview', slug: 'architecture' },
                    { label: 'System Overview', slug: 'architecture/overview' },
                    { label: 'Data Flow', slug: 'architecture/data-flow' },
                    { label: 'Azure Infrastructure', slug: 'architecture/infrastructure' },
                ],
            },
            {
                label: 'Services',
                items: [
                    { label: 'Overview', slug: 'services' },
                    { label: 'MCP Server', slug: 'services/mcp-server' },
                    { label: 'MCP Server (.NET)', slug: 'services/mcp-server-dotnet' },
                    { label: 'Agent API', slug: 'services/agent-api' },
                    { label: 'Agent API (.NET)', slug: 'services/agent-api-dotnet' },
                    { label: 'Auth API', slug: 'services/auth-api' },
                    { label: 'Load Generator', slug: 'services/load-generator' },
                    { label: 'UI', slug: 'services/ui' },
                ],
            },
            {
                label: 'Data Pipeline',
                items: [
                    { label: 'Overview', slug: 'data-pipeline' },
                    { label: 'NBI Bridge Refresh', slug: 'data-pipeline/nbi-refresh' },
                    { label: 'FEMA Disaster Refresh', slug: 'data-pipeline/fema-refresh' },
                    { label: 'EIA Energy Refresh', slug: 'data-pipeline/eia-refresh' },
                    { label: 'TWDB Water Plan Refresh', slug: 'data-pipeline/twdb-refresh' },
                    { label: 'Knowledge Base Init', slug: 'data-pipeline/knowledge-base-init' },
                ],
            },
            {
                label: 'Observability',
                items: [
                    { label: 'Overview', slug: 'observability' },
                    { label: 'APM & Tracing', slug: 'observability/apm' },
                    { label: 'RUM & Session Replay', slug: 'observability/rum' },
                    { label: 'Dashboards & Monitors', slug: 'observability/dashboards' },
                ],
            },
            {
                label: 'LLM Engineering Guide',
                items: [
                    { label: 'Overview', slug: 'llm-engineering' },
                    { label: 'Quickstart', slug: 'llm-engineering/quickstart' },
                    {
                        label: 'Instrumentation',
                        collapsed: true,
                        items: [
                            { label: 'Python (ddtrace)', slug: 'llm-engineering/instrumentation/python' },
                            { label: '.NET (OpenTelemetry)', slug: 'llm-engineering/instrumentation/dotnet' },
                        ],
                    },
                    {
                        label: 'Monitoring',
                        collapsed: true,
                        items: [
                            { label: 'Spans and traces', slug: 'llm-engineering/monitoring/spans-and-traces' },
                            { label: 'APM correlation', slug: 'llm-engineering/monitoring/apm-correlation' },
                            { label: 'MCP clients', slug: 'llm-engineering/monitoring/mcp-clients' },
                            { label: 'Prompt tracking', slug: 'llm-engineering/monitoring/prompt-tracking' },
                            { label: 'Metrics', slug: 'llm-engineering/monitoring/metrics' },
                            { label: 'Operations', slug: 'llm-engineering/monitoring/operations' },
                        ],
                    },
                    {
                        label: 'Evaluations',
                        collapsed: true,
                        items: [
                            { label: 'Managed', slug: 'llm-engineering/evaluations/managed' },
                            { label: 'LLM-as-Judge (DD UI)', slug: 'llm-engineering/evaluations/llm-judge-ui' },
                            { label: 'External', slug: 'llm-engineering/evaluations/external' },
                            { label: 'Annotation queues', slug: 'llm-engineering/evaluations/annotation-queues' },
                            { label: 'Export API', slug: 'llm-engineering/evaluations/export-api' },
                            { label: 'Developer guide', slug: 'llm-engineering/evaluations/developer-guide' },
                        ],
                    },
                    { label: 'Experiments', slug: 'llm-engineering/experiments' },
                    { label: 'Datadog MCP server', slug: 'llm-engineering/datadog-mcp' },
                    { label: 'Data security & RBAC', slug: 'llm-engineering/security-rbac' },
                    { label: 'Glossary', slug: 'llm-engineering/glossary' },
                ],
            },
            {
                label: 'Deployment',
                items: [
                    { label: 'Overview', slug: 'deployment' },
                    { label: 'Prerequisites', slug: 'deployment/prerequisites' },
                    { label: 'Quickstart', slug: 'deployment/quickstart' },
                    { label: 'Kubernetes Resources', slug: 'deployment/kubernetes' },
                ],
            },
            {
                label: 'Development',
                items: [
                    { label: 'Overview', slug: 'development' },
                    { label: 'Local Setup', slug: 'development/local-setup' },
                    { label: 'Testing', slug: 'development/testing' },
                    { label: 'Conventions', slug: 'development/conventions' },
                ],
            },
            {
                label: 'Agent Guides',
                items: [
                    { label: 'Project Map', slug: 'agent-guides/project-map' },
                    { label: 'Core Conventions', slug: 'agent-guides/core-conventions' },
                    { label: 'Build, Test & Verify', slug: 'agent-guides/build-test-verify' },
                ],
            },
            { label: 'Resource Group Migration', slug: 'resource-group-migration' },
        ],
    }), mdx()],
});