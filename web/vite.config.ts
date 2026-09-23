import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const apiTarget = process.env.API_PROXY_TARGET ?? "http://127.0.0.1:8000";
const proxy = {
  "/api": {
    target: apiTarget,
    changeOrigin: true,
  },
};

export default defineConfig({
  plugins: [react()],
  cacheDir: "/tmp/review-agent-vite",
  server: {
    host: "0.0.0.0",
    port: 5173,
    strictPort: true,
    proxy,
  },
  preview: {
    host: "0.0.0.0",
    port: 5173,
    strictPort: true,
    proxy,
  },
});
