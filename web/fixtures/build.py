"""Rebuild `web/fixtures/dashboard.json`, the site's fallback data.

Run from the repo root: `uv run python web/fixtures/build.py`.

The site imports `data/site/dashboard.json` when the pipeline has written one and this
file otherwise (see `web/vite.config.ts`), so `pnpm build` and `pnpm test` work on a clean
checkout. It is produced from the same seeded database the Python tests use, so a change
to the export shows up here as a diff rather than as two fixtures drifting apart.

Two departures from the S14 recipe:

- `generated_at` is **pinned**. The export window is anchored on it, so letting it default
  to now would change the fixture's shape every day and make every committed diff noise.
  The date chosen reads as fresh against a clock in September 2026; the stale-data and
  empty-state cases are built in the Vitest suite from this file rather than committed as
  more copies of it.
- The seeded database has no `news_event` rows, so the feed, the chart pins and the drawer
  would all render their empty state and nothing would exercise them. The rows below are
  hand-written in the shape `news/ingest.py` writes: dates inside GDELT's 90-day archive
  (which is why every pin sits left of `generated_at`), one per severity, and an
  `impact_note` that is the ingest's own note about the row rather than a fare estimate.
"""

import sys
from datetime import date, datetime
from pathlib import Path

import duckdb

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tests"))

from conftest import seed_analytics_db  # noqa: E402

from flight_detective import db  # noqa: E402
from flight_detective.export import site  # noqa: E402
from flight_detective.models import NewsEvent  # noqa: E402

GENERATED_AT = datetime.fromisoformat("2026-09-09T06:35:00+02:00")

EVENTS = (
    NewsEvent(
        event_date=date(2026, 6, 24),
        category="airspace",
        severity=3,
        headline="Middle East airspace restriction extended through the summer",
        source_url="https://avherald.com/h?article=51f2a0c1",
        dedupe_key="airspace-2026-06-24-middle-east-restriction",
        impact_note="4 outlets · severity 3 from 'airspace closed'",
    ),
    NewsEvent(
        event_date=date(2026, 7, 12),
        category="regulatory",
        severity=1,
        headline="EASA publishes routine airworthiness directive review for EU operations",
        source_url="https://www.easa.europa.eu/en/newsroom/ad-review-2026",
        dedupe_key="regulatory-2026-07-12-easa-ad-review",
        impact_note="1 outlet · severity 1, no restriction keyword matched",
    ),
    NewsEvent(
        event_date=date(2026, 8, 3),
        category="pilgrimage",
        severity=2,
        headline="Umrah quota raised for European pilgrims ahead of the winter season",
        source_url="https://www.arabnews.com/node/2026-umrah-quota",
        dedupe_key="pilgrimage-2026-08-03-umrah-quota",
        impact_note="3 outlets · severity 2 from 'quota'",
    ),
    NewsEvent(
        event_date=date(2026, 8, 27),
        category="disruption",
        severity=3,
        headline="French ATC strike notice covers a 48-hour window at CDG",
        source_url="https://www.dgac.fr/fr/preavis-de-greve-2026",
        dedupe_key="disruption-2026-08-27-cdg-atc-strike",
        impact_note="6 outlets · severity 3 from 'strike'",
    ),
    NewsEvent(
        event_date=date(2026, 9, 2),
        category="regulatory",
        severity=2,
        headline="PIA adds a third weekly CDG-ISB direct for the wedding peak",
        source_url="https://www.reuters.com/business/aerospace-defense/pia-cdg-isb",
        dedupe_key="regulatory-2026-09-02-pia-third-weekly",
        impact_note="2 outlets · severity 2 from 'capacity'",
    ),
)


def main() -> None:
    conn = duckdb.connect(":memory:")
    seed_analytics_db(conn)
    db.upsert_news_events(conn, EVENTS)
    out = REPO / "web" / "fixtures" / "dashboard.json"
    site.write_dashboard(site.build_dashboard(conn, generated_at=GENERATED_AT), out)
    print(f"wrote {out.relative_to(REPO)}")


if __name__ == "__main__":
    main()
