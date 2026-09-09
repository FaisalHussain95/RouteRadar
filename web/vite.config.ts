import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const here = fileURLToPath(new URL(".", import.meta.url));

// The pipeline's own export when the box has written one, the committed fixture otherwise,
// resolved at config time so a clean checkout builds and tests without running the pipeline.
// It is an *import*, not a fetch: the JSON ends up inside the hashed JS bundle, which is what
// keeps the deployed page a set of immutable files with no runtime request for data.
const EXPORTED = resolve(here, "../data/site/dashboard.json");
const FIXTURE = resolve(here, "fixtures/dashboard.json");

export default defineConfig({
  // GitHub Pages serves a project site under /<repo>/, so that is the default. A root deploy
  // (a user/org site, or a local `vite preview`) sets VITE_BASE=/ instead of editing this.
  base: process.env.VITE_BASE ?? "/flight-detective/",
  plugins: [react()],
  resolve: {
    alias: { "@dashboard-data": existsSync(EXPORTED) ? EXPORTED : FIXTURE },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
