import { useEffect, useMemo, useRef, useState } from "react";

import { FareChart, type HoverState } from "./components/FareChart";
import { EventDrawer } from "./components/EventDrawer";
import { EventFeed } from "./components/EventFeed";
import { FilterBar } from "./components/FilterBar";
import { Header } from "./components/Header";
import { ArbitrageCard } from "./components/modules/ArbitrageCard";
import { EfficiencyCard } from "./components/modules/EfficiencyCard";
import { SeasonalGaugeCard } from "./components/modules/SeasonalGaugeCard";
import {
  chartEmptyState,
  exportWindow,
  hasAnyFares,
  hoverDates,
  seriesFor,
  type Filters,
} from "./lib/select";
import { newsFreshness, staleness } from "./lib/staleness";
import type { DashboardData, Event } from "./types";

/** The line every region shows before the pipeline has ever run. One sentence, one place:
 * "no data yet" is a statement about the whole page, so the cards must not each phrase it
 * differently — and none of them may print a `€0` or a `+0%` instead. */
const WAITING = "Waiting for the first ingest";

function initialFilters(data: DashboardData): Filters {
  return { destination: data.destinations[0]?.code ?? "ISB", hiddenCarriers: new Set() };
}

export function App({ data, now }: { data: DashboardData; now?: Date }) {
  const [filters, setFilters] = useState<Filters>(() => initialFilters(data));
  const [hover, setHover] = useState<HoverState | null>(null);
  const [open, setOpen] = useState<{ event: Event; opener: HTMLElement | null } | null>(null);
  const plotRef = useRef<HTMLDivElement>(null);

  const freshness = staleness(data.generated_at, now ?? new Date());
  // Anchored on `generated_at`, not on `now`: see `newsFreshness`. The two signals are
  // deliberately separate — the header's banner is about the file, this one is about the
  // news half of the pipeline having stopped while the fares kept arriving.
  const news = newsFreshness(data.news_status, data.generated_at);
  const range = useMemo(() => exportWindow(data), [data]);
  const series = useMemo(() => seriesFor(data, filters), [data, filters]);
  const days = useMemo(() => hoverDates(series), [series]);
  const empty = chartEmptyState(data, filters, series);
  const noDataYet = !hasAnyFares(data);

  // A tap outside the plot clears a stuck hairline (§ Phone behaviour). While the drawer is
  // open it does not, or reading the drawer would lose the date the reader opened it from;
  // the backdrop clears it explicitly, which is the one "outside" the spec calls out.
  useEffect(() => {
    if (hover === null || !hover.stuck || open !== null) return;
    const onDown = (e: PointerEvent) => {
      const target = e.target;
      if (target instanceof Node && plotRef.current?.contains(target)) return;
      setHover(null);
    };
    document.addEventListener("pointerdown", onDown);
    return () => {
      document.removeEventListener("pointerdown", onDown);
    };
  }, [hover, open]);

  // Escape clears the hairline. When the drawer is open it never gets here: the drawer
  // listens in the capture phase and stops the event, so one Escape closes one thing.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setHover(null);
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
    };
  }, []);

  return (
    <>
      <Header data={data} freshness={freshness} />
      <FilterBar data={data} filters={filters} onChange={setFilters} />

      <div className="main-grid">
        <FareChart
          data={data}
          filters={filters}
          series={series}
          days={days}
          range={range}
          empty={empty}
          hover={hover}
          onHover={setHover}
          onOpenEvent={(event, opener) => setOpen({ event, opener })}
          plotRef={plotRef}
        />
        <EventFeed
          events={data.events}
          emptyLine={noDataYet ? WAITING : "No news events in the last 90 days"}
          news={news}
          onOpen={(event, opener) => setOpen({ event, opener })}
        />
      </div>

      <div className="modules-grid">
        <ArbitrageCard arbitrage={noDataYet ? null : data.arbitrage} />
        <EfficiencyCard
          efficiency={noDataYet ? [] : data.efficiency}
          carriers={data.carriers}
          emptyLine={noDataYet ? WAITING : "No carrier averages yet"}
        />
        <SeasonalGaugeCard
          gauge={noDataYet ? null : data.seasonal_gauge}
          emptyLine={noDataYet ? WAITING : "Not enough fares for a seasonal comparison"}
        />
      </div>

      {open !== null && (
        <EventDrawer
          event={open.event}
          opener={open.opener}
          onClose={() => setOpen(null)}
          onBackdropPointerDown={() => setHover(null)}
        />
      )}
    </>
  );
}
