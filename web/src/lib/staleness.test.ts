import { describe, expect, it } from "vitest";

import { staleness, STALE_AFTER_HOURS } from "./staleness";

const GENERATED_AT = "2026-09-09T06:35:00+02:00";

describe("staleness", () => {
  it("says nothing while the data is fresh", () => {
    const s = staleness(GENERATED_AT, new Date("2026-09-09T18:35:00+02:00"));
    expect(s.stale).toBe(false);
    expect(s.message).toBeNull();
    expect(s.ageHours).toBeCloseTo(12);
  });

  it("holds its tongue at exactly the threshold", () => {
    const at = new Date(Date.parse(GENERATED_AT) + STALE_AFTER_HOURS * 3_600_000);
    expect(staleness(GENERATED_AT, at).stale).toBe(false);
    expect(staleness(GENERATED_AT, new Date(at.getTime() + 60_000)).stale).toBe(true);
  });

  it("names the age and the last run once a day has been missed", () => {
    const s = staleness(GENERATED_AT, new Date("2026-09-10T22:35:00+02:00"));
    expect(s.stale).toBe(true);
    expect(s.ageDays).toBe(1);
    expect(s.message).toBe(
      "Data is 1 day old — the daily ingest has not run since 9 Sep 2026",
    );
  });

  it("pluralises past the second day", () => {
    const s = staleness(GENERATED_AT, new Date("2026-09-12T06:35:00+02:00"));
    expect(s.ageDays).toBe(3);
    expect(s.message).toContain("Data is 3 days old");
  });
});
