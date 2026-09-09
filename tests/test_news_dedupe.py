"""S10: one row per incident, not per outlet.

The fixtures carry the two shapes that matter: the same story from two outlets with
different source credits and a reworded headline (must collapse), and two different
stories on the same day (must not). The threshold assertions below are what stop a
future tweak from quietly merging unrelated events.
"""

from datetime import date

import pytest
from conftest import load_gdelt_body as load_fixture

from flight_detective.news.dedupe import (
    SIMILARITY_THRESHOLD,
    Candidate,
    dedupe_key,
    group_incidents,
    normalize_title,
    similarity,
)
from flight_detective.news.gdelt import Article, Category, parse_articles


def candidates(slug: str, category: Category) -> list[Candidate]:
    return [Candidate(category=category, article=a) for a in parse_articles(load_fixture(slug))]


# --- normalisation ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "EASA lifts ban on PIA flights to Europe | Dawn",
            "easa lifts ban on pia flights to europe",
        ),
        ("CDG strike grounds flights - Reuters", "cdg strike grounds flights"),
        ("Grève à CDG : des vols annulés", "greve a cdg des vols annules"),
        ("  Spaced   out   ", "spaced out"),
    ],
)
def test_normalize_strips_credit_accents_punctuation_and_space(raw: str, expected: str) -> None:
    assert normalize_title(raw) == expected


def test_a_long_tail_after_a_dash_is_not_a_source_credit() -> None:
    title = "Pakistan reopens its airspace - and the winter fares that closed it are falling back"
    assert normalize_title(title).endswith("falling back")


# --- similarity -------------------------------------------------------------------------


def test_the_same_story_from_two_outlets_scores_above_the_threshold() -> None:
    assert (
        similarity(
            "EASA lifts its ban on PIA flights to Europe - Reuters",
            "EASA lifts ban on PIA flights to Europe | Dawn",
        )
        >= SIMILARITY_THRESHOLD
    )


def test_a_reworded_headline_for_one_incident_still_scores_above_the_threshold() -> None:
    assert (
        similarity(
            "CDG strike grounds hundreds of flights at Roissy - Le Monde",
            "Strike at CDG grounds hundreds of flights",
        )
        >= SIMILARITY_THRESHOLD
    )


def test_a_rewrite_that_keeps_only_the_content_words_still_matches() -> None:
    # Character-level similarity scores this pair 0.49 and would split it; content-word
    # overlap is what makes a reordered headline still one incident.
    assert (
        similarity(
            "Flights to Europe resume: EASA lifts the PIA ban",
            "EASA lifts the PIA ban on flights to Europe",
        )
        >= SIMILARITY_THRESHOLD
    )


def test_two_different_same_day_stories_score_well_below_the_threshold() -> None:
    assert (
        similarity(
            "CDG strike grounds hundreds of flights at Roissy - Le Monde",
            "Doha transit delay stretches connection times to five hours",
        )
        < SIMILARITY_THRESHOLD / 2
    )


@pytest.mark.parametrize(
    ("left", "right"),
    [
        # The population that can actually collide: same taxonomy group, same day, one
        # salient word apart. Every pair here scores 0.79-0.92 on character similarity,
        # which is why that measure was replaced.
        (
            "PIA suspends Paris flights from Monday",
            "PIA suspends Lahore flights from Monday",
        ),
        ("Saudi Arabia cuts the Umrah quota", "Saudi Arabia cuts the Hajj quota"),
        (
            "Pakistan closes airspace to Indian carriers",
            "Pakistan closes airspace to all carriers",
        ),
        (
            "EASA bans PIA from European airspace",
            "EASA lifts ban on PIA in European airspace",
        ),
    ],
)
def test_same_topic_different_incidents_stay_below_the_threshold(left: str, right: str) -> None:
    assert similarity(left, right) < SIMILARITY_THRESHOLD


def test_a_reversal_is_not_merged_into_the_event_it_reverses() -> None:
    day = date(2026, 9, 1)
    incidents = group_incidents(
        [
            Candidate(
                category="regulatory",
                article=Article(
                    url="https://a.test/1",
                    title="EASA bans PIA from European airspace",
                    seendate="20260901T080000Z",
                ),
            ),
            Candidate(
                category="regulatory",
                article=Article(
                    url="https://b.test/2",
                    title="EASA lifts ban on PIA in European airspace",
                    seendate="20260901T090000Z",
                ),
            ),
        ]
    )
    assert len(incidents) == 2
    assert [i.event_date for i in incidents] == [day, day]


def test_titles_sharing_only_function_words_score_zero() -> None:
    assert similarity("Of the and to", "In the and by") == 0.0


# --- grouping ---------------------------------------------------------------------------


def test_same_incident_two_outlets_becomes_one_incident() -> None:
    incidents = group_incidents(candidates("regulatory-easa-pia", "regulatory"))
    assert len(incidents) == 2
    merged = incidents[0]
    assert [a.domain for a in merged.articles] == ["reuters.com", "dawn.com"]
    # The earliest article is the canonical one, and it is what the row is built from.
    assert merged.canonical.domain == "reuters.com"
    assert incidents[1].canonical.domain == "aviacionline.com"


def test_different_stories_on_one_day_stay_separate() -> None:
    incidents = group_incidents(candidates("airspace-disruption", "airspace_disruption"))
    assert len(incidents) == 2
    assert [len(i.articles) for i in incidents] == [2, 1]
    assert incidents[1].canonical.domain == "thenationalnews.com"


def test_the_same_headline_on_two_days_is_two_incidents() -> None:
    first, second = parse_articles(load_fixture("airspace-disruption"))[:2]
    later = second.model_copy(update={"seendate": "20260903T091500Z"})
    incidents = group_incidents(
        [
            Candidate(category="airspace_disruption", article=first),
            Candidate(category="airspace_disruption", article=later),
        ]
    )
    assert [i.event_date for i in incidents] == [date(2026, 9, 2), date(2026, 9, 3)]


def test_grouping_does_not_depend_on_the_order_gdelt_listed_the_articles() -> None:
    items = candidates("regulatory-easa-pia", "regulatory")
    forwards = group_incidents(items)
    backwards = group_incidents(list(reversed(items)))
    assert [i.dedupe_key for i in forwards] == [i.dedupe_key for i in backwards]


def test_an_incident_matched_by_two_taxonomy_groups_is_filed_once() -> None:
    articles = parse_articles(load_fixture("regulatory-easa-pia"))[:2]
    incidents = group_incidents(
        [
            Candidate(category="regulatory", article=articles[0]),
            Candidate(category="airspace_disruption", article=articles[1]),
        ]
    )
    assert len(incidents) == 1
    assert incidents[0].category == "regulatory"


def test_no_candidates_is_no_incidents() -> None:
    assert group_incidents([]) == []


def test_domains_are_distinct_and_in_first_seen_order() -> None:
    article = Article(url="u", title="t", seendate="20260901T000000Z", domain="dawn.com")
    incident = group_incidents(
        [
            Candidate(category="regulatory", article=article),
            Candidate(
                category="regulatory",
                article=article.model_copy(update={"url": "u2", "seendate": "20260901T010000Z"}),
            ),
        ]
    )[0]
    assert incident.domains == ["dawn.com"]


# --- keys -------------------------------------------------------------------------------


def test_dedupe_key_is_readable_dated_and_stable_across_source_credits() -> None:
    key = dedupe_key(date(2026, 9, 1), "EASA lifts ban on PIA flights to Europe | Dawn")
    assert key.startswith("2026-09-01:easa-lifts-ban-on-pia-flights-to-")
    assert key == dedupe_key(date(2026, 9, 1), "EASA lifts ban on PIA flights to Europe - Reuters")


def test_dedupe_key_separates_two_stories_that_share_their_first_words() -> None:
    day = date(2026, 9, 1)
    first = dedupe_key(day, "Pakistan closes its airspace to all traffic from Wednesday")
    second = dedupe_key(day, "Pakistan closes its airspace to all traffic from Thursday")
    assert first != second


def test_dedupe_key_of_an_untitled_story_is_still_a_key() -> None:
    assert dedupe_key(date(2026, 9, 1), "!!!").startswith("2026-09-01:untitled-")
