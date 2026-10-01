import { defineConfig } from "vite";
import { fileURLToPath } from "node:url";
// @ts-ignore
import vue from "@vitejs/plugin-vue";
import { createApiProxyOptions } from "./electron/api-auth-proxy.mjs";

const apiTarget = process.env.VITE_API_PROXY_TARGET ?? "http://127.0.0.1:1032";
const apiAuthToken = process.env.API_AUTH_TOKEN ?? "";

export default defineConfig({
  plugins: [vue()],
  resolve: {
    // texmath's require fallback and the explicit engine import must share one
    // renderer; KaTeX exports separate CJS/ESM implementations by default.
    alias: [{ find: /^katex$/, replacement: fileURLToPath(new URL("./node_modules/katex/dist/katex.mjs", import.meta.url)) }],
  },
  build: {
    rollupOptions: {
      output: {
        // Keep shared CommonJS helpers outside optional renderers: notifications
        // also use them and must not preload syntax highlighting at startup.
        onlyExplicitManualChunks: true,
        manualChunks(id) {
          const path = id.replace(/\\/g, "/");
          if (/\/node_modules\/(?:@vue\/|vue\/|vue-router\/|pinia\/)/.test(path)) return "vendor-vue";
          if (/\/node_modules\/katex\/.*\.m?js/.test(path)) return "vendor-math";
          if (/\/node_modules\/highlight\.js\/.*\.js/.test(path)) return "vendor-highlight";
          if (/\/src\/locales\/(?:zh-CN|en-US)\.ts$/.test(path)) return "locale-base";
        },
      },
    },
  },
  server: {
    host: "127.0.0.1",
    port: 5175,
    strictPort: true,
    proxy: {
      "/api": {
        ...createApiProxyOptions(apiTarget, apiAuthToken),
      },
    },
  },
});
