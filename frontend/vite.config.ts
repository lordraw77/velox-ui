import { svelte } from "@sveltejs/vite-plugin-svelte";
import { defineConfig } from "vite";
import { fileURLToPath } from "node:url";

/**
 * The build output goes straight into the Python package, so a wheel carries the
 * interface with it and `pip install velox-ui` needs no Node at runtime — one of the
 * project's anti-requirements.
 */
export default defineConfig({
  plugins: [svelte()],
  resolve: {
    alias: { $lib: fileURLToPath(new URL("./src/lib", import.meta.url)) },
  },
  build: {
    outDir: "../src/velox_ui/web",
    emptyOutDir: true,
    target: "es2022",
    // Source maps are shipped: this is self-hosted software and the people running it
    // are the people who file its bug reports.
    sourcemap: true,
    rollupOptions: {
      output: {
        // Keep the entry chunk honest. Markdown, highlighting and anything else that
        // is not needed to show the first token belongs in its own chunk, loaded when
        // it is actually wanted (ADR-0012).
        manualChunks: undefined,
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8080", changeOrigin: true },
      "/health": "http://127.0.0.1:8080",
      "/ready": "http://127.0.0.1:8080",
    },
  },
  worker: { format: "es" },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
