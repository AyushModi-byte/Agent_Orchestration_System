import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const API = process.env.VITE_API_URL ?? "http://localhost:8000";
const routes = ["/tasks", "/approvals", "/traces", "/metrics", "/memory", "/replays"];

// In dev the API is proxied so the browser sees one origin (SSE and CORS just work).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: Object.fromEntries(routes.map((r) => [`/api${r}`, { target: API, rewrite: (p: string) => p.replace(/^\/api/, "") }])),
  },
});
