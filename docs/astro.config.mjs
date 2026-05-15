// @ts-check
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';
import lucode from 'lucode-starlight';

export default defineConfig({
    site: 'https://kyletaylored.github.io',
    base: '/infra-advisor-ai',
    integrations: [
        starlight({
            title: 'InfraAdvisor AI',
            description: 'AI-powered infrastructure advisory platform for AEC/O&M consulting firms',
            social: [
                {
                    icon: 'github',
                    label: 'GitHub',
                    href: 'https://github.com/kyletaylored/infra-advisor-ai',
                },
            ],
            lastUpdated: true,
            head: [
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
                        { label: 'LLM Observability', slug: 'observability/llm-observability' },
                        { label: 'LLM Observability (.NET)', slug: 'observability/llm-observability-dotnet' },
                        { label: 'RUM & Session Replay', slug: 'observability/rum' },
                        { label: 'Dashboards & Monitors', slug: 'observability/dashboards' },
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
        }),
    ],
});
