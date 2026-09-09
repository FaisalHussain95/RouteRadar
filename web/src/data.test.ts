import { describe, expect, it } from "vitest";

import { dashboardData, parseDashboard, SUPPORTED_SCHEMA_VERSION } from "./data";
import { FIXTURE } from "./test/fixtures";

describe("parseDashboard", () => {
  it("accepts the committed export", () => {
    expect(parseDashboard(FIXTURE).schema_version).toBe(SUPPORTED_SCHEMA_VERSION);
  });

  it("refuses a file this build's types were not generated from", () => {
    expect(() => parseDashboard({ ...FIXTURE, schema_version: 3 })).toThrow(/schema_version 3/);
    expect(() => parseDashboard(null)).toThrow(/not an object/);
  });

  it("is what the bundled data resolves to, with no fetch involved", () => {
    expect(dashboardData.schema_version).toBe(SUPPORTED_SCHEMA_VERSION);
  });
});
