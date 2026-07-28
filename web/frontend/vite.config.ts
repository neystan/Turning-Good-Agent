import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const backendUrl = process.env.VITE_BACKEND_URL ?? "http://127.0.0.1:8000";
const backendWebSocketUrl = backendUrl.replace(/^http/, "ws");

export default defineConfig({
  plugins: [react()],
  build: { outDir: "../static", emptyOutDir: true },
  server: {
    watch: { usePolling: process.env.CHOKIDAR_USEPOLLING === "true" },
    proxy: { "/api": backendUrl, "/ws": { target: backendWebSocketUrl, ws: true } }
  }
});
