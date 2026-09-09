"""The daily news ingest (PRD F4): taxonomy queries in, deduplicated `news_event` rows out.

The bargain is `run_ingest`'s, transplanted: a `GdeltError` on one taxonomy query costs
that query and nothing else. Everything that answered is grouped, scored and written, the
failures are recorded in `ingest_run.error`, and the CLI only fails the command when
*every* query died. News is context around the fares, not the dataset — one dead query
must not cost the day's export, and a run that gathered nothing shows up as an
`ingest_run` row with `rows_kept = 0`, which is exactly what that table is for.

## The severity heuristic

`severity` is 1–3 and comes from keywords in the headline. It is a heuristic and it is
meant to be revised from data, like the calendar multipliers:

- **3 — capacity is gone.** An airspace closure, a regulator's ban, a suspension, a
  grounding, a strike, a war. These are the events that move a fare by tens of percent.
- **2 — capacity is strained.** Delays, disruption, cancellations, quota cuts,
  reroutes, restrictions: schedules bend, prices drift.
- **1 — context, no operational signal.** Everything else the taxonomy caught. Kept
  rather than dropped: the taxonomy is already narrow, and S12 wants to be able to say
  "nothing much happened" with evidence.

Matching is on the normalised headline with word boundaries, so "cut" does not fire on
"executive" and an accented French headline scores like its English wire copy. The
highest band that matches wins, and the matching keyword goes into `impact_note` so a
surprising severity can be traced to the word that caused it without re-running anything.

**The scale measures capacity *lost*, so it has to read direction.** Half of what the
regulatory taxonomy catches is a restriction being *undone* — "EASA lifts its ban on PIA
flights", "airspace reopened", "strike called off" — and those carry the same band-3
keyword as the event they reverse. A maximum-severity marker sitting on a fare *drop* is
worse for S12 than no marker at all, so a `REVERSAL_CUES` word anywhere in a band-3
headline demotes the row to 1 and the cue is named in `impact_note`. The story is still
stored: a ban being lifted explains a price move, it is just not a shock. Demotion applies
to band 3 only — band 2 is already mild enough that reading direction into it would
over-fit.

The cue list only holds verbs that occur in **no other sense**, because the demotion drops
straight from 3 to 1 and a false demotion is as bad as the bug it fixes. "end"/"ends" and
"revoked" were tried and removed: "Airspace closure extended to the **end** of October",
"Air France strike **ends** its third day", "PIA suspends flights after its licence was
**revoked**" are all live capacity losses that those words would have scored 1. Adjacency
to the keyword does not rescue them ("strike ends" is adjacent and still wrong), so the
list is kept narrow instead. Do not add a cue that can appear in a non-reversal headline.
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

import duckdb

from flight_detective import db
from flight_detective.models import IngestRun, NewsEvent
from flight_detective.news.dedupe import Candidate, Incident, group_incidents, normalize_title
from flight_detective.news.gdelt import (
    DEFAULT_DAYS,
    PARIS,
    REQUEST_INTERVAL_SECONDS,
    TAXONOMY,
    GdeltClient,
    GdeltError,
    TaxonomyQuery,
)

__all__ = ["DEFAULT_DAYS", "NewsIngestResult", "Severity", "run_news_ingest", "severity_for"]

# Highest band first: the first that matches wins. Phrases are matched whole, on the
# normalised title, between word boundaries. See the module docstring for what the
# bands mean; these are inputs to revise from observed fare moves, not conclusions.
SEVERITY_SIGNALS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (
        3,
        (
            "airspace closed",
            "airspace closure",
            "closes airspace",
            "shuts airspace",
            "ban",
            "bans",
            "banned",
            "suspends",
            "suspended",
            "suspension",
            "grounded",
            "grounds",
            "strike",
            "strikes",
            "shutdown",
            "war",
            "missile",
        ),
    ),
    (
        2,
        (
            "delay",
            "delays",
            "delayed",
            "disruption",
            "disrupted",
            "restriction",
            "restrictions",
            "restricted",
            "quota",
            "quotas",
            "cancels",
            "cancelled",
            "canceled",
            "cancellations",
            "cut",
            "cuts",
            "halt",
            "halts",
            "halted",
            "reroute",
            "rerouted",
            "diverted",
        ),
    ),
)

DEFAULT_SEVERITY = 1
TOP_SEVERITY = 3

# Words that mean the restriction in the same headline is being undone. Only checked
# against a band-3 match; see the module docstring for why direction matters at all, and
# for the cues ("end", "ends", "revoked") that were tried and removed as ambiguous.
REVERSAL_CUES: tuple[str, ...] = (
    "lift",
    "lifts",
    "lifted",
    "called off",
    "reopen",
    "reopens",
    "reopened",
    "resume",
    "resumes",
    "resumed",
    "restore",
    "restores",
    "restored",
    "overturned",
    "averted",
    "avoided",
)


def _word_pattern(words: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile(r"\b(?:" + "|".join(re.escape(word) for word in words) + r")\b")


_SEVERITY_PATTERNS: tuple[tuple[int, re.Pattern[str]], ...] = tuple(
    (level, _word_pattern(words)) for level, words in SEVERITY_SIGNALS
)
_REVERSAL_PATTERN = _word_pattern(REVERSAL_CUES)


@dataclass(frozen=True)
class Severity:
    """A scored headline: the band, the keyword that set it, and the cue that lowered it."""

    level: int
    keyword: str | None = None
    reversed_by: str | None = None


def severity_for(title: str) -> Severity:
    """Score one headline. The highest matching band wins; a band-3 match next to a
    reversal cue is demoted to 1, because the scale measures capacity lost."""
    normalized = normalize_title(title)
    for level, pattern in _SEVERITY_PATTERNS:
        match = pattern.search(normalized)
        if match is None:
            continue
        if level == TOP_SEVERITY and (cue := _REVERSAL_PATTERN.search(normalized)):
            return Severity(DEFAULT_SEVERITY, match.group(0), cue.group(0))
        return Severity(level, match.group(0))
    return Severity(DEFAULT_SEVERITY)


def impact_note(incident: Incident, severity: Severity) -> str:
    """Why this row looks the way it does: how many outlets carried it, and the keyword."""
    domains = incident.domains
    outlets = f"{len(incident.articles)} article{'s' if len(incident.articles) != 1 else ''}"
    where = f" ({', '.join(domains)})" if domains else ""
    if severity.keyword is None:
        return f"{outlets}{where}; no severity keyword"
    signal = f"severity keyword {severity.keyword!r}"
    if severity.reversed_by is not None:
        signal += f", reversed by {severity.reversed_by!r}"
    return f"{outlets}{where}; {signal}"


def to_news_event(incident: Incident) -> NewsEvent:
    """One incident as the `news_event` row for it."""
    severity = severity_for(incident.canonical.title)
    return NewsEvent(
        event_date=incident.event_date,
        category=incident.category,
        severity=severity.level,
        headline=incident.canonical.title,
        source_url=incident.canonical.url,
        dedupe_key=incident.dedupe_key,
        impact_note=impact_note(incident, severity),
    )


@dataclass(frozen=True)
class NewsIngestResult:
    """What one `fd news-ingest` did: the logged run plus the rows it wrote."""

    run: IngestRun
    events: list[NewsEvent]


def run_news_ingest(
    conn: duckdb.DuckDBPyConnection,
    client: GdeltClient,
    *,
    days: int = DEFAULT_DAYS,
    taxonomy: Sequence[TaxonomyQuery] = TAXONOMY,
    interval: float = REQUEST_INTERVAL_SECONDS,
    sleep: Callable[[float], None] | None = None,
) -> NewsIngestResult:
    """Run every taxonomy query, collapse duplicates, upsert, and log the run.

    Idempotent: the rows land on `dedupe_key`, so re-running the same window rewrites the
    same rows instead of adding any. `rows_dropped` is the number of articles that
    collapsed into an existing incident."""
    run_at = datetime.now(PARIS)
    pause = client.sleep if sleep is None else sleep
    candidates: list[Candidate] = []
    errors: list[str] = []
    for index, entry in enumerate(taxonomy):
        if index:
            pause(interval)
        try:
            articles = client.search(entry.query, days)
        except GdeltError as exc:
            # One failure per line, whitespace flattened, exactly as `ingest_run.error`
            # is written for a fare cell.
            errors.append(f"{entry.slug}: {' '.join(str(exc).split())}")
            continue
        candidates.extend(Candidate(category=entry.category, article=a) for a in articles)

    incidents = group_incidents(candidates)
    events = [to_news_event(incident) for incident in incidents]
    run = IngestRun(
        run_at=run_at,
        provider=client.name,
        queries=len(taxonomy),
        rows_kept=len(events),
        rows_dropped=len(candidates) - len(events),
        error="\n".join(errors) or None,
    )
    conn.begin()
    try:
        db.upsert_news_events(conn, events)
        db.insert_ingest_run(conn, run)
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    return NewsIngestResult(run=run, events=events)
