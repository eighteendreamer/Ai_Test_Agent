import { defineConfig } from "vite";
// @ts-ignore
import vue from "@vitejs/plugin-vue";

const apiTarget = process.env.VITE_API_PROXY_TARGET ?? "http://127.0.0.1:1032";

export default defineConfig({
  plugins: [vue()],
  server: {
    host: "127.0.0.1",
    port: 5175,
    strictPort: true,
    proxy: {
      "/api": {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
});
