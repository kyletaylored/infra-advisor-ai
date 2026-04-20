import React from "react";
import { createRoot } from "react-dom/client";
import { ChakraProvider, createSystem, defaultConfig, defineConfig } from "@chakra-ui/react";
import App from "./App";
import "./index.css";

const config = defineConfig({
  theme: {
    tokens: {
      colors: {
        brand: {
          50: { value: "#EBF5FF" },
          100: { value: "#BEE3F8" },
          500: { value: "#3B82F6" },
          600: { value: "#2563EB" },
          700: { value: "#1D4ED8" },
          800: { value: "#1E40AF" },
        },
      },
      fonts: {
        body: { value: "Inter, system-ui, -apple-system, sans-serif" },
        heading: { value: "Inter, system-ui, -apple-system, sans-serif" },
        mono: { value: "JetBrains Mono, Menlo, monospace" },
      },
    },
  },
  globalCss: {
    "html, body, #root": { height: "100%" },
  },
});

const system = createSystem(defaultConfig, config);

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ChakraProvider value={system}>
      <App />
    </ChakraProvider>
  </React.StrictMode>
);
