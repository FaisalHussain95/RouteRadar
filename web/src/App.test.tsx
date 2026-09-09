import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import {
  beforeFirstIngest,
  FIXTURE,
  NOW_12H_LATER,
  NOW_40H_LATER,
  withoutModules,
} from "./test/fixtures";
import type { DashboardData } from "./types";

function show(data: DashboardData = FIXTURE, now: Date = NOW_12H_LATER) {
  return render(<App data={data} now={now} />);
}

let fetchSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  // The whole point of the static build: the page is fed by a bundled import, so a fetch
  // anywhere in it is a regression however well the page happens to render.
  fetchSpy = vi.spyOn(globalThis, "fetch");
});

afterEach(() => {
  expect(fetchSpy).not.toHaveBeenCalled();
  vi.restoreAllMocks();
});

describe("the page, from the committed fixture", () => {
  it("renders every region of the design", () => {
    show();

    expect(screen.getByText("RouteRadar")).toBeInTheDocument();
    expect(screen.getByText("CDG → PK-NORTH")).toBeInTheDocument();
    expect(screen.getByText(/Max 1 Stop/)).toBeInTheDocument();

    // Filter bar: origin, three destinations plus the computed All, six carriers, six horizons.
    expect(screen.getByText("Paris · fixed")).toBeInTheDocument();
    const dests = within(screen.getByRole("group", { name: "Destination" })).getAllByRole("button");
    expect(dests.map((b) => b.textContent)).toEqual(["ISB", "LHE", "SKT", "All · compare"]);
    expect(within(screen.getByRole("group", { name: "Carriers" })).getAllByRole("button")).toHaveLength(6);
    const horizons = within(screen.getByRole("group", { name: "Booking horizon" })).getAllByRole("button");
    expect(horizons.map((b) => b.textContent)).toEqual(["14d", "30d", "60d", "90d", "120d", "180d"]);

    // Chart: bands from the calendar engine, a line per carrier, a pin per event.
    expect(screen.getByRole("heading", { name: "Lowest fare by carrier" })).toBeInTheDocument();
    expect(document.querySelectorAll("[data-testid^='band-']").length).toBe(FIXTURE.bands.length);
    expect(document.querySelectorAll("[data-testid^='series-']").length).toBe(6);
    expect(screen.getAllByRole("button", { name: /severity event:/ })).toHaveLength(
      FIXTURE.events.length,
    );

    // Legend: the six carrier keys S13 added, then the bands and the pin key.
    for (const c of FIXTURE.carriers) {
      expect(screen.getByTestId(`legend-carrier-${c.code}`)).toHaveTextContent(c.code);
    }
    expect(screen.getByText("Wedding season")).toBeInTheDocument();
    expect(screen.getByText(/News event/)).toBeInTheDocument();

    // Feed, with `date · source · severity` rather than the design's derived impact score.
    const feed = screen.getByRole("heading", { name: "Event feed" }).closest("aside");
    expect(within(feed!).getAllByRole("button")).toHaveLength(FIXTURE.events.length);
    expect(screen.getByText(/24 Jun 2026 · avherald\.com · high/)).toBeInTheDocument();

    // Modules.
    expect(screen.getByText("−€90")).toBeInTheDocument();
    expect(screen.getByText(/spread LHE − SKT on 23 Sep 2026/)).toBeInTheDocument();
    expect(screen.getByText("Fly LHE + M-11 transfer")).toBeInTheDocument();
    expect(screen.getByText(/saves €56 after €34 ground transfer/)).toBeInTheDocument();
    expect(screen.getByTestId("efficiency-dot-PK")).toBeInTheDocument();
    expect(screen.getByText("+69%")).toBeInTheDocument();
    expect(screen.getByText(FIXTURE.seasonal_gauge!.method)).toBeInTheDocument();
  });

  it("filters client-side, without a fetch", async () => {
    const user = userEvent.setup();
    show();

    // ISB at 14d is PIA and Qatar; Lahore at 14d is Gulf Air alone.
    const hasPoints = (code: string) =>
      (document.querySelector(`[data-testid='series-${code}']`)?.getAttribute("d") ?? "") !== "";
    expect(hasPoints("PK")).toBe(true);
    expect(hasPoints("GF")).toBe(false);

    await user.click(screen.getByRole("button", { name: "LHE" }));
    expect(hasPoints("GF")).toBe(true);
    expect(hasPoints("PK")).toBe(false);

    // A hidden carrier leaves the chart and greys in the legend; nothing else repaints.
    await user.click(within(screen.getByRole("group", { name: "Carriers" })).getByRole("button", { name: /Gulf Air/ }));
    expect(document.querySelector("[data-testid='series-GF']")).toBeNull();
    expect(screen.getByTestId("legend-carrier-GF")).toHaveStyle({ opacity: "0.4" });

    await user.click(screen.getByRole("button", { name: "90d" }));
    expect(screen.getByRole("button", { name: "90d" })).toHaveAttribute("aria-pressed", "true");
  });
});

describe("the stale-data banner", () => {
  it("stays quiet on data 12 h old", () => {
    show(FIXTURE, NOW_12H_LATER);
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.getByTestId("status-dot")).not.toHaveClass("stale");
    expect(screen.getByText(/Updated 9 Sep 2026/)).toBeInTheDocument();
  });

  it("names the age and the last run on data 40 h old", () => {
    show(FIXTURE, NOW_40H_LATER);
    expect(screen.getByRole("status")).toHaveTextContent(
      "Data is 1 day old — the daily ingest has not run since 9 Sep 2026",
    );
    expect(screen.getByTestId("status-dot")).toHaveClass("stale");
    expect(screen.queryByText(/Daily cron ingest/)).toBeNull();
  });
});

describe("empty states", () => {
  it("says the ingest has never run, and still shades the calendar", async () => {
    const user = userEvent.setup();
    show(beforeFirstIngest());

    expect(screen.getByTestId("chart-empty-no-data-yet")).toHaveTextContent(
      "No fares ingested yet · the first run is scheduled for 06:30 CET",
    );
    // bands[] comes from the calendar engine, not from fares, so it is populated first.
    expect(document.querySelectorAll("[data-testid^='band-']").length).toBe(FIXTURE.bands.length);

    for (const id of ["feed-empty", "efficiency-empty", "gauge-empty"]) {
      expect(screen.getByTestId(id)).toHaveTextContent("Waiting for the first ingest");
    }
    expect(screen.getByTestId("arbitrage-empty")).toBeInTheDocument();

    // The filter bar renders and stays interactive.
    await user.click(screen.getByRole("button", { name: "SKT" }));
    expect(screen.getByRole("button", { name: "SKT" })).toHaveAttribute("aria-pressed", "true");
  });

  it("blames the horizon when the data is there but the filter selects nothing", async () => {
    const user = userEvent.setup();
    show();
    await user.click(screen.getByRole("button", { name: "60d" }));

    expect(screen.getByTestId("chart-empty-no-combination")).toHaveTextContent(
      "No fares for Islamabad at 60d — try another horizon",
    );
    expect(screen.queryByTestId("chart-empty-no-data-yet")).toBeNull();
    // The axes, bands and pins stay: it is a gap in one cell, not an empty page.
    expect(document.querySelectorAll("[data-testid^='band-']").length).toBe(FIXTURE.bands.length);
    expect(screen.getAllByRole("button", { name: /severity event:/ })).toHaveLength(
      FIXTURE.events.length,
    );
    expect(screen.getByText("€500")).toBeInTheDocument();
  });

  it("blames the chips when every carrier is off, and not the data", async () => {
    const user = userEvent.setup();
    show();
    for (const c of FIXTURE.carriers) {
      await user.click(
        within(screen.getByRole("group", { name: "Carriers" })).getByRole("button", {
          name: new RegExp(`${c.code}$`),
        }),
      );
    }
    expect(screen.getByTestId("chart-empty-no-carriers")).toHaveTextContent("All carriers hidden");
    expect(screen.queryByTestId("chart-empty-no-combination")).toBeNull();
  });

  it("gives each module its own line rather than a zero", () => {
    show(withoutModules());

    expect(screen.getByTestId("arbitrage-empty")).toHaveTextContent(/a spread needs two fares/);
    expect(screen.getByTestId("efficiency-empty")).toHaveTextContent("No carrier averages yet");
    expect(screen.getByTestId("gauge-empty")).toHaveTextContent(
      "Not enough fares for a seasonal comparison",
    );
    expect(screen.getByTestId("feed-empty")).toHaveTextContent("No news events in the last 90 days");

    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/€0\b/);
    expect(text).not.toMatch(/NaN/);
    expect(text).not.toMatch(/[+−]0%/);
    // …and it must not borrow the "never ingested" wording, which would be a lie here.
    expect(text).not.toContain("Waiting for the first ingest");
  });
});

describe("the event drawer", () => {
  it("is modal: focus, scroll lock, Escape, and focus back to the opener", async () => {
    const user = userEvent.setup();
    show();
    const row = within(
      screen.getByRole("heading", { name: "Event feed" }).closest("aside")!,
    ).getAllByRole("button")[0];

    await user.click(row);
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(document.activeElement).toBe(within(dialog).getByRole("heading", { level: 2 }));
    expect(document.body.style.overflow).toBe("hidden");
    // impact_text is the ingest's own note, not the "Est. fare impact" the design labels it.
    expect(within(dialog).getByText("Ingest note")).toBeInTheDocument();
    expect(within(dialog).queryByText(/Est\. fare impact/)).toBeNull();
    // body is null in v1, so the drawer says so rather than showing an empty paragraph.
    expect(within(dialog).getByText(/GDELT indexes headlines and links/)).toBeInTheDocument();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.body.style.overflow).toBe("");
    expect(document.activeElement).toBe(row);
  });

  it("traps Tab inside itself", async () => {
    const user = userEvent.setup();
    show();
    await user.click(screen.getAllByRole("button", { name: /severity event:/ })[0]);
    const dialog = screen.getByRole("dialog");
    const items = within(dialog).getAllByRole("button").concat(within(dialog).getAllByRole("link"));

    await user.tab();
    expect(dialog.contains(document.activeElement)).toBe(true);
    for (let i = 0; i < items.length + 2; i++) await user.tab();
    expect(dialog.contains(document.activeElement)).toBe(true);
  });

  it("closes on a backdrop tap", async () => {
    const user = userEvent.setup();
    show();
    await user.click(screen.getAllByRole("button", { name: /severity event:/ })[0]);
    await user.click(screen.getByRole("button", { name: "Close event details" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("hover, on a pointer and on a finger", () => {
  const plot = () => screen.getByRole("group", { name: /Lowest fare by carrier/ });

  it("tracks the mouse and clears when it leaves", () => {
    show();
    fireEvent.pointerMove(plot(), { pointerType: "mouse", clientX: 10 });
    expect(screen.getByTestId("hairline")).toBeInTheDocument();
    fireEvent.pointerLeave(plot(), { pointerType: "mouse" });
    expect(screen.queryByTestId("hairline")).toBeNull();
  });

  it("sticks on a tap and survives the finger lifting", () => {
    show();
    fireEvent.pointerDown(plot(), { pointerType: "touch", clientX: 10 });
    expect(screen.getByTestId("hover-card")).toBeInTheDocument();
    fireEvent.pointerLeave(plot(), { pointerType: "touch" });
    expect(screen.getByTestId("hairline")).toBeInTheDocument();
  });

  it("clears on a tap outside the plot and on Escape", () => {
    show();
    fireEvent.pointerDown(plot(), { pointerType: "touch", clientX: 10 });
    fireEvent.pointerDown(document.body);
    expect(screen.queryByTestId("hairline")).toBeNull();

    fireEvent.pointerDown(plot(), { pointerType: "touch", clientX: 10 });
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByTestId("hairline")).toBeNull();
  });

  it("is reachable from the keyboard alone", () => {
    show();
    plot().focus();
    fireEvent.keyDown(plot(), { key: "ArrowRight" });
    expect(screen.getByTestId("hover-card")).toBeInTheDocument();
    fireEvent.keyDown(plot(), { key: "End" });
    expect(screen.getByTestId("hairline")).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByTestId("hairline")).toBeNull();
  });

  it("keeps the hairline while the drawer is open, so closing returns to the same date", async () => {
    const user = userEvent.setup();
    show();
    fireEvent.pointerDown(plot(), { pointerType: "touch", clientX: 10 });
    await user.click(screen.getAllByRole("button", { name: /severity event:/ })[0]);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByTestId("hairline")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByTestId("hairline")).toBeInTheDocument();
  });
});
