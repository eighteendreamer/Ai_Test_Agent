import { defineConfig } from "vite";
// @ts-ignore
import vue from "@vitejs/plugin-vue";

const apiTarget = process.env.VITE_API_PROXY_TARGET ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5175,
    proxy: {
      "/api": {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
});
