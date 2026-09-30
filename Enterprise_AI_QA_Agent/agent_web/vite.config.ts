import { defineConfig } from "vite";
// @ts-ignore
import vue from "@vitejs/plugin-vue";
import { createApiProxyOptions } from "./electron/api-auth-proxy.mjs";

const apiTarget = process.env.VITE_API_PROXY_TARGET ?? "http://127.0.0.1:1032";
const apiAuthToken = process.env.API_AUTH_TOKEN ?? "";

export default defineConfig({
  plugins: [vue()],
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
