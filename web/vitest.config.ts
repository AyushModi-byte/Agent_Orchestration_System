import { defineConfig } from "vitest/config";

// Kept apart from vite.config.ts: vitest bundles its own vite, and mixing plugin types breaks tsc.
export default defineConfig({
  esbuild: { jsx: "automatic" },
  test: { environment: "jsdom", globals: true, setupFiles: ["./src/test/setup.ts"], css: false },
});
