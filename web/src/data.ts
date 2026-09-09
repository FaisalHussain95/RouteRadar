import raw from "@dashboard-data";

import type { DashboardData } from "./types";

/** The version `src/types.ts` was generated from. */
export const SUPPORTED_SCHEMA_VERSION = 2;

/** The file, checked far enough to know the generated types describe it.
 *
 * A full validation would need the schema at runtime and would only restate what
 * `fd export-site` already guarantees; what it cannot guarantee is that the deployed
 * bundle and the committed JSON are the same generation, so that is what is checked. A
 * mismatch throws rather than rendering half a page from fields that have moved. */
export function parseDashboard(value: unknown): DashboardData {
  if (typeof value !== "object" || value === null) {
    throw new Error("dashboard.json is not an object");
  }
  const version = (value as { schema_version?: unknown }).schema_version;
  if (version !== SUPPORTED_SCHEMA_VERSION) {
    throw new Error(
      `dashboard.json is schema_version ${String(version)}, this build reads ` +
        `${SUPPORTED_SCHEMA_VERSION}: re-run \`pnpm gen\` against the current schema`,
    );
  }
  return value as DashboardData;
}

export const dashboardData: DashboardData = parseDashboard(raw);
