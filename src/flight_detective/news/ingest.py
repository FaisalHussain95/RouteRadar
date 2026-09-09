"""The daily news ingest (PRD F4): GDELT Cloud queries in, `news_event` rows out.

The bargain is `run_ingest`'s, transplanted: a `GdeltCloudError` on one query costs that
query and nothing else. Everything that answered is filtered, scored and written, the
failures are recorded in `ingest_run.error`, and the CLI only fails the command when
*every* query died. News is context around the fares, not the dataset — one dead query
must not cost the day's export, and a run that gathered nothing shows up as an
`ingest_run` row with `rows_kept = 0`, which is exactly what that table is for.

**Quota is the one failure that is not per-query.** `GdeltCloudQuotaError` means the
month's units are gone, so every remaining query would fail identically; the loop stops
there and records the queries it never issued. See `news/gdeltcloud.py` § Errors.

## Deduplication is the API's job now

A GDELT Cloud Story *is* a cluster of articles about one development, so `dedupe_key` is
the story id and the title-similarity grouping that `news/dedupe.py` used to do is gone
with the DOC client. What remains is cross-*query* deduplication: nine queries over one
week overlap heavily — "UAE flights disrupted by Iran tensions" came back from three of
them in the recorded fixtures — and the same story must produce one row, not three.
First arm to return it names it, and the carrier queries run first so a story about an
airline is filed under that airline's category rather than under whichever semantic pool
also swept it up.

## The severity heuristic

`severity` is 1-3 and comes from `metrics.significance`, the cluster's own measure of how
widely a development was carried. On the recorded fixtures significance is a clean
monotone function of cluster size — 1 article scores 0.0753, 2 scores 0.1193, 3 scores
0.1505, 4 scores 0.1747, i.e. `0.0753 * log2(n + 1)` to four figures — so the bands below
are stated as significance (what the API gives) and mirrored as article counts (what the
number means), and `SEVERITY_BANDS` carries both.

- **3 — a major development.** Above the 90th percentile of the recorded rows, about nine
  outlets or more. At this size a story is a regional event, not an item.
- **2 — carried widely.** Above the 75th percentile, about three outlets or more.
- **1 — a single report.** Everything else, which on a normal week is most of it. Kept
  rather than dropped: the guard in `news/gdeltcloud.py` has already decided the story is
  about these routes, and S12 wants to be able to say "nothing much happened" with
  evidence.

**This scale measures how loudly something was reported, not how much capacity it cost.**
That is a deliberate trade and it is worth knowing which way it cuts: the DOC-era heuristic
read keywords in the headline and could tell "EASA bans PIA" from "EASA lifts its ban on
PIA", demoting the reversal. Significance cannot — a ban and its lifting are both big
news and both score 3. What it gets in exchange is a number the API measured across every
outlet that carried the story, rather than a keyword list this project has to maintain
against wire-copy phrasing in two languages. `impact_note` prints the article count and
the keyword the story was kept on, so a surprising severity is traceable without a re-run.
If the dashboard later needs direction rather than volume, that is a signal to add beside
severity, not a reason to go back to scoring headlines by hand.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import duckdb

from flight_detective import db
from flight_detective.models import IngestRun, NewsEvent
from flight_detective.news.gdeltcloud import (
    CARRIERS,
    DEFAULT_DAYS,
    REQUEST_INTERVAL_SECONDS,
    SEMANTIC_QUERIES,
    Carrier,
    Category,
    GdeltCloudClient,
    GdeltCloudError,
    GdeltCloudQuotaError,
    QueryUnits,
    SemanticQuery,
    Story,
    Walk,
    carrier_slug,
    headline,
    is_relevant,
    source_url,
)

__all__ = [
    "DEFAULT_DAYS",
    "NewsIngestResult",
    "Severity",
    "run_news_ingest",
    "severity_for",
]

PARIS = ZoneInfo("Europe/Paris")

# One query, deferred so the loop below can time, space and fail them uniformly whichever
# retrieval arm they came from.
_Job = Callable[[], Walk]

# The category an entity query's stories get unless a matched keyword reclassifies them.
# A carrier is retrieved because it is a carrier, and what usually happens to a carrier in
# the news is regulatory; airspace and pilgrimage stories are moved by their keyword.
CARRIER_CATEGORY: Category = "regulatory"

# Highest band first: the first that matches wins. `significance` is the API's measure and
# the threshold that decides; `articles` is the same cut expressed in cluster size, both
# for the note and so the band can be applied when significance was not measured. See the
# module docstring for where the numbers come from — they are the 90th and 75th percentiles
# of the recorded fixtures, not a scale the API documents.
SEVERITY_BANDS: tuple[tuple[int, float, int], ...] = (
    (3, 0.25, 9),
    (2, 0.15, 3),
)

DEFAULT_SEVERITY = 1


@dataclass(frozen=True)
class Severity:
    """A scored story: the band, and the measurement that set it."""

    level: int
    significance: float | None = None
    article_count: int = 0


def severity_for(story: Story) -> Severity:
    """Score one story by how widely it was carried.

    `significance` decides. When the API did not measure it — a `null` metric means
    unmeasured, never zero — the cluster's article count stands in at the equivalent cut,
    which keeps a big story from being filed as a single report just because one number
    was missing."""
    significance = story.metrics.significance
    articles = story.metrics.article_count
    for level, floor, min_articles in SEVERITY_BANDS:
        if significance is not None and significance >= floor:
            return Severity(level, significance, articles)
        if significance is None and articles >= min_articles:
            return Severity(level, None, articles)
    return Severity(DEFAULT_SEVERITY, significance, articles)


def impact_note(story: Story, severity: Severity, keyword: str) -> str:
    """Why this row looks the way it does: how widely it was carried, and what kept it."""
    count = story.metrics.article_count
    outlets = f"{count} article{'s' if count != 1 else ''}"
    domains = sorted({a.domain for a in story.top_articles if a.domain})
    where = f" ({', '.join(domains)})" if domains else ""
    measure = (
        f"significance {severity.significance:.4f}"
        if severity.significance is not None
        else "significance unmeasured"
    )
    return f"{outlets}{where}; {measure}; kept on {keyword!r}"


@dataclass(frozen=True)
class NewsIngestResult:
    """What one `fd news-ingest` did: the logged run, the rows it wrote, and the budget.

    `units` is `None` when `/meta/query-units` itself failed — free or not, it is one more
    call that can time out, and losing the budget reading is not a reason to skip the run."""

    run: IngestRun
    events: list[NewsEvent]
    units: QueryUnits | None = None


def _to_news_event(story: Story, category: Category, keyword: str) -> NewsEvent:
    """One Story as the `news_event` row for it."""
    severity = severity_for(story)
    return NewsEvent(
        event_date=story.story_date,
        category=category,
        severity=severity.level,
        headline=headline(story),
        source_url=source_url(story),
        # The story id *is* the cluster identity, so re-ingesting an overlapping window
        # rewrites the same rows rather than adding any.
        dedupe_key=story.id,
        impact_note=impact_note(story, severity, keyword),
    )


def run_news_ingest(
    conn: duckdb.DuckDBPyConnection,
    client: GdeltCloudClient,
    *,
    days: int = DEFAULT_DAYS,
    carriers: Sequence[Carrier] = CARRIERS,
    semantic: Sequence[SemanticQuery] = SEMANTIC_QUERIES,
    interval: float = REQUEST_INTERVAL_SECONDS,
    sleep: Callable[[float], None] | None = None,
) -> NewsIngestResult:
    """Run every query, apply the relevance guard, upsert what survives, and log the run.

    Idempotent: rows land on `dedupe_key`, which is the story id, so re-running the same
    window rewrites the same rows instead of adding any. `rows_dropped` counts the stories
    the guard rejected plus the duplicates other queries had already claimed."""
    run_at = datetime.now(PARIS)
    pause = client.sleep if sleep is None else sleep
    units = _query_units(client)

    events: dict[str, NewsEvent] = {}
    errors: list[str] = []
    fetched = 0
    issued = 0
    # Carriers first: see the module docstring on which arm gets to name a shared story.
    jobs: list[tuple[str, Category, _Job]] = [
        *((carrier_slug(c), CARRIER_CATEGORY, _carrier_job(client, c, days)) for c in carriers),
        *((q.slug, q.category, _search_job(client, q, days)) for q in semantic),
    ]
    for index, (slug, category, job) in enumerate(jobs):
        if index:
            pause(interval)
        issued += 1
        try:
            walk = job()
        except GdeltCloudQuotaError as exc:
            # Every remaining query would fail the same way, so stop rather than spend the
            # run discovering it once per job. The queries never issued are named, because
            # a short run with no explanation reads like a quiet week.
            errors.append(f"{slug}: {_flat(exc)}")
            skipped = [name for name, _, _ in jobs[index + 1 :]]
            if skipped:
                errors.append(f"not issued after quota exhausted: {', '.join(skipped)}")
            break
        except GdeltCloudError as exc:
            # One failure per line, whitespace flattened, exactly as `ingest_run.error`
            # is written for a fare cell.
            errors.append(f"{slug}: {_flat(exc)}")
            continue
        if walk.truncated:
            errors.append(f"{slug}: stopped at the row cap; the query is matching too broadly")
        for story in walk.stories:
            fetched += 1
            keyword = is_relevant(story)
            if keyword is None or story.id in events:
                continue
            events[story.id] = _to_news_event(story, keyword.category or category, keyword.word)

    kept = list(events.values())
    run = IngestRun(
        run_at=run_at,
        provider=client.name,
        queries=issued,
        rows_kept=len(kept),
        rows_dropped=fetched - len(kept),
        error="\n".join(errors) or None,
    )
    conn.begin()
    try:
        db.upsert_news_events(conn, kept)
        db.insert_ingest_run(conn, run)
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    return NewsIngestResult(run=run, events=kept, units=units)


def _carrier_job(client: GdeltCloudClient, carrier: Carrier, days: int) -> _Job:
    return lambda: client.stories_for_carrier(carrier, days)


def _search_job(client: GdeltCloudClient, query: SemanticQuery, days: int) -> _Job:
    return lambda: client.stories_for_search(query, days)


def _query_units(client: GdeltCloudClient) -> QueryUnits | None:
    """The month's remaining budget, or None if even the free call failed."""
    try:
        return client.query_units()
    except GdeltCloudError:
        return None


def _flat(exc: Exception) -> str:
    return " ".join(str(exc).split())
