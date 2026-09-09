"""One row per incident, not per outlet (PRD F4).

A wire story about an airspace closure is republished by ten sites within an hour, and a
fare chart with ten identical markers on one day is worse than useless. Two articles are
the same incident when they were **seen on the same day** and their **normalised titles
are similar enough**; that is the whole rule, and it is deliberately dumber than topic
clustering: GDELT gives no cluster id in `artlist`, and Event Registry (which does) is the
open question the PRD parks for v1.

Normalisation is what makes the comparison work at all — it strips the trailing source
credit ("… - Reuters", "… | Dawn"), folds accents, drops punctuation and collapses
whitespace. Similarity is then **Jaccard overlap of the content words**: the normalised
titles minus a short list of function words, compared as sets.

Character-level similarity (`difflib.SequenceMatcher`) was tried first and is the wrong
tool, which is worth recording because it looks right. Two headlines that differ in one
salient word are almost identical as strings: "PIA suspends Paris flights from Monday" vs
"… Lahore flights …" scores 0.909, "Pakistan closes airspace to Indian carriers" vs "… to
all carriers" 0.916, "EASA bans PIA from European airspace" vs "EASA lifts ban on PIA in
European airspace" 0.795 — all *different incidents*, all above any threshold that still
merges a genuine reword. On content-word overlap those same pairs score 0.67, 0.67 and
0.57 while real duplicates score 0.83–1.00, so `SIMILARITY_THRESHOLD = 0.75` sits in a
wide gap. The tests pin both populations.

Erring towards a false *split* is deliberate: two markers on a chart for one story is a
cosmetic problem, while a false merge silently deletes an event and files the survivor
under the wrong headline. "all" is therefore not a function word — it is what distinguishes
a total airspace closure from a targeted one.

Grouping is greedy against each group's *canonical* article, and the canonical is the
earliest `(seen_at, url)` in the group. Candidates are sorted the same way first, so the
grouping — and therefore `dedupe_key` — does not depend on the order GDELT happened to
list the articles in. The consequence to know: a rolling window eventually drops the
canonical article, and the same incident then gets a new key and a second row. That is
accepted; the alternative is fuzzy-matching new incidents against the whole table.

`dedupe_key` is `<event_date>:<slug of the canonical title>-<hash>`, readable enough to
recognise in a `SELECT` and short enough for a primary key. The hash disambiguates two
different incidents on one day whose first words agree.
"""

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import date

from flight_detective.news.gdelt import Article, Category, seen_at, seen_date

SIMILARITY_THRESHOLD = 0.75

# Function words only: they carry no incident-identifying information, and dropping them is
# what lets "EASA lifts its ban on PIA" and "EASA lifts ban on PIA" compare equal. Nothing
# that names, quantifies or negates belongs here.
FUNCTION_WORDS = frozenset(
    # fmt: off
    (
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "as",
        "at",
        "by",
        "for",
        "from",
        "in",
        "into",
        "of",
        "on",
        "onto",
        "to",
        "with",
        "without",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "has",
        "have",
        "had",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "after",
        "amid",
        "over",
        "up",
        "out",
        "about",
    )
    # fmt: on
)

# The trailing source credit outlets append: " - Reuters", " | Dawn", " – Le Monde".
# Bounded length so a headline that merely contains a dash keeps its second half.
_SOURCE_CREDIT = re.compile(r"\s+[-–—|]\s+[^-–—|]{1,40}$")
_NON_WORD = re.compile(r"[^a-z0-9 ]+")

# Enough words to recognise the story, few enough to keep the key short.
_SLUG_WORDS = 8
_HASH_LENGTH = 8


@dataclass(frozen=True)
class Candidate:
    """An article ready to be grouped: its category and the day it belongs to."""

    category: Category
    article: Article

    @property
    def event_date(self) -> date:
        return seen_date(self.article)

    @property
    def order(self) -> tuple[str, str]:
        """Deterministic sort key: when GDELT saw it, then its URL to break ties."""
        return (seen_at(self.article).isoformat(), self.article.url)


@dataclass(frozen=True)
class Incident:
    """One event: the canonical article plus every duplicate that collapsed into it."""

    event_date: date
    category: Category
    articles: tuple[Article, ...]

    @property
    def canonical(self) -> Article:
        return self.articles[0]

    @property
    def dedupe_key(self) -> str:
        return dedupe_key(self.event_date, self.canonical.title)

    @property
    def domains(self) -> list[str]:
        """Distinct source domains, in first-seen order."""
        seen: list[str] = []
        for article in self.articles:
            if article.domain and article.domain not in seen:
                seen.append(article.domain)
        return seen


def normalize_title(title: str) -> str:
    """Lowercase, accent-folded, credit-stripped, punctuation-free form used to compare."""
    folded = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    stripped = _SOURCE_CREDIT.sub("", folded.lower())
    return " ".join(_NON_WORD.sub(" ", stripped).split())


def content_words(title: str) -> frozenset[str]:
    """The normalised title's words with the function words removed."""
    return frozenset(normalize_title(title).split()) - FUNCTION_WORDS


def similarity(left: str, right: str) -> float:
    """0..1 Jaccard overlap of two titles' content words. Two headlines with nothing but
    function words in common score 0, and so does a title that has no content words."""
    first, second = content_words(left), content_words(right)
    union = first | second
    return len(first & second) / len(union) if union else 0.0


def dedupe_key(event_date: date, title: str) -> str:
    """The primary key of `news_event`: stable for one incident on one day."""
    normalized = normalize_title(title)
    slug = "-".join(normalized.split()[:_SLUG_WORDS]) or "untitled"
    digest = hashlib.sha256(normalized.encode()).hexdigest()[:_HASH_LENGTH]
    return f"{event_date.isoformat()}:{slug}-{digest}"


def group_incidents(
    candidates: list[Candidate], *, threshold: float = SIMILARITY_THRESHOLD
) -> list[Incident]:
    """Collapse same-day, same-story articles into one `Incident` each.

    Returned in date then canonical-article order. A candidate joins the first group on
    its day whose canonical title it resembles; the category of that first article is the
    incident's, so an incident matched by two taxonomy groups is filed under the earlier
    one rather than stored twice.
    """
    groups: dict[date, list[list[Candidate]]] = {}
    for candidate in sorted(candidates, key=lambda c: c.order):
        day = groups.setdefault(candidate.event_date, [])
        for group in day:
            if similarity(group[0].article.title, candidate.article.title) >= threshold:
                group.append(candidate)
                break
        else:
            day.append([candidate])
    return [
        Incident(
            event_date=event_date,
            category=group[0].category,
            articles=tuple(c.article for c in group),
        )
        for event_date in sorted(groups)
        for group in groups[event_date]
    ]
