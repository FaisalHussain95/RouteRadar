import { describe, expect, it } from "vitest";

import { newsFreshness, staleness, STALE_AFTER_HOURS } from "./staleness";

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

const FRESH = { last_success: "2026-09-09", last_error: null };

describe("newsFreshness", () => {
  it("says nothing while the news step is keeping up", () => {
    const n = newsFreshness(FRESH, GENERATED_AT);
    expect(n.message).toBeNull();
    expect(n.detail).toBeNull();
  });

  it("tolerates a gap no single missed run explains", () => {
    // The threshold is the *last* day that is still quiet, so two days of silence is one
    // bad night plus its retry, and three is a pattern.
    const two = { ...FRESH, last_success: "2026-09-07" };
    expect(newsFreshness(two, GENERATED_AT).message).toBeNull();
    const three = { ...FRESH, last_success: "2026-09-06" };
    expect(newsFreshness(three, GENERATED_AT).message).toContain("News unavailable");
  });

  it("names the day the news last worked", () => {
    const n = newsFreshness({ ...FRESH, last_success: "2026-09-04" }, GENERATED_AT);
    expect(n.message).toBe("News unavailable since 4 Sep 2026");
  });

  it("carries the run's own reason as the detail", () => {
    const n = newsFreshness(
      { last_success: "2026-09-04", last_error: "GDELT Cloud query units exhausted after 2 queries" },
      GENERATED_AT,
    );
    expect(n.detail).toBe("GDELT Cloud query units exhausted after 2 queries");
  });

  it("keeps quiet before the news step has ever run", () => {
    // Nothing has succeeded and nothing has failed: this is a fresh database, which the
    // page already states once, as "waiting for the first ingest".
    const n = newsFreshness({ last_success: null, last_error: null }, GENERATED_AT);
    expect(n.message).toBeNull();
    expect(n.detail).toBeNull();
  });

  it("speaks up when the news step has run and never once succeeded", () => {
    const n = newsFreshness(
      { last_success: null, last_error: "pia: timed out (+8 more)" },
      GENERATED_AT,
    );
    expect(n.message).toBe("News unavailable — no run has succeeded yet");
    expect(n.detail).toBe("pia: timed out (+8 more)");
  });

  it("does not read a stale error as a stale feed", () => {
    // S17's ingest logs one line per dead query even on a run that kept rows, so a healthy
    // feed routinely exports a `last_error`. It is the reason, not the trigger.
    expect(
      newsFreshness({ ...FRESH, last_error: "cdg-strikes: timed out" }, GENERATED_AT).message,
    ).toBeNull();
  });
});
