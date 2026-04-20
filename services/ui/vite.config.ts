import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      "/api": {
        target: process.env.VITE_AGENT_API_URL || "http://localhost:8001",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
  build: {
    outDir: "dist",
    // 'hidden' generates sourcemaps without the //# sourceMappingURL comment —
    // they are uploaded to Datadog RUM but never served to end-users.
    sourcemap: "hidden",
  },
});
