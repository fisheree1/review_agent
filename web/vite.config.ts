import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const apiTarget = process.env.API_PROXY_TARGET ?? "http://127.0.0.1:8000";
const apiToken = process.env.LOCAL_API_TOKEN;

if (!apiToken) {
  throw new Error("LOCAL_API_TOKEN is required for the local API proxy");
}

const proxy = {
  "/api": {
    target: apiTarget,
    changeOrigin: true,
    configure(proxyServer: { on: (event: string, handler: (request: { setHeader: (name: string, value: string) => void }) => void) => void }) {
      proxyServer.on("proxyReq", (proxyRequest) => {
        proxyRequest.setHeader("Authorization", `Bearer ${apiToken}`);
      });
    },
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
