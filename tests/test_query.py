from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from crickey.query import (
    PlayerPageSpec,
    Qualification,
    QuerySpecError,
    ResolvedPeriod,
    SeasonPeriod,
    StatsguruQuery,
    SymbolicPeriod,
    SymbolicPeriodKind,
    player_search_url,
)


def test_babar_t20i_comparison_golden_url_exact() -> None:
    query = StatsguruQuery(
        **{
            "class": 3,
            "type": "batting",
            "period": ResolvedPeriod(start=date(2016, 9, 7), end=date(2026, 2, 24)),
            "qualifications": (
                Qualification(field="runs", minimum=1000),
                Qualification(field="batting_average", minimum=Decimal("38.94")),
                Qualification(field="batting_strike_rate", minimum=Decimal("128.02")),
            ),
            "orderby": "batting_average",
            "size": 200,
        }
    )

    assert query.results_url(as_of=date(2026, 10, 4)) == (
        "https://stats.cricinfo.com/ci/engine/stats/index.html?"
        "class=3;orderby=batting_average;qualmin1=1000;qualmin2=38.94;"
        "qualmin3=128.02;qualval1=runs;qualval2=batting_average;"
        "qualval3=batting_strike_rate;size=200;spanmax1=24+Feb+2026;"
        "spanmin1=07+Sep+2016;spanval1=span;template=results;type=batting"
    )


def test_babar_world_cup_player_page_has_trophy_and_pinned_dates() -> None:
    page = PlayerPageSpec(player_id=348144, **{"class": 2, "type": "batting", "trophy": 12})

    assert page.url(as_of=date(2026, 10, 4)) == (
        "https://stats.cricinfo.com/ci/engine/player/348144.html?"
        "class=2;spanmax1=04+Oct+2026;spanmin1=05+Jan+1971;spanval1=span;"
        "template=results;trophy=12;type=batting"
    )


def test_odi_hundreds_query_adds_default_pinned_dates() -> None:
    query = StatsguruQuery(
        **{
            "class": 2,
            "type": "batting",
            "qualifications": (Qualification(field="hundreds", minimum=10),),
            "orderby": "hundreds",
            "size": 200,
        }
    )

    assert query.results_url(as_of=date(2026, 10, 4)) == (
        "https://stats.cricinfo.com/ci/engine/stats/index.html?"
        "class=2;orderby=hundreds;qualmin1=10;qualval1=hundreds;size=200;"
        "spanmax1=04+Oct+2026;spanmin1=05+Jan+1971;spanval1=span;"
        "template=results;type=batting"
    )


def test_alphabetical_repeated_keys_and_date_edge_formatting() -> None:
    query = StatsguruQuery(
        **{
            "class": 3,
            "type": "team",
            "result": [1, 2],
            "period": ResolvedPeriod(start=date(2024, 2, 29), end=date(2024, 3, 7)),
            "size": 10,
        }
    )

    assert query.results_url() == (
        "https://stats.cricinfo.com/ci/engine/stats/index.html?"
        "class=3;result=1;result=2;size=10;spanmax1=07+Mar+2024;"
        "spanmin1=29+Feb+2024;spanval1=span;template=results;type=team"
    )


def test_narrower_period_overrides_default_pinning_and_as_of_is_injectable() -> None:
    all_time = StatsguruQuery(**{"class": 3, "type": "batting"})
    narrower = StatsguruQuery(
        **{
            "class": 3,
            "type": "batting",
            "period": ResolvedPeriod(start=date(2020, 1, 1), end=date(2021, 12, 31)),
        }
    )

    assert "spanmin1=17+Feb+2005" in all_time.results_url(as_of=date(2026, 10, 4))
    assert "spanmax1=04+Oct+2026" in all_time.results_url(as_of=date(2026, 10, 4))
    assert "spanmin1=01+Jan+2020" in narrower.results_url(as_of=date(2026, 10, 4))
    assert "spanmax1=31+Dec+2021" in narrower.results_url(as_of=date(2026, 10, 4))


@pytest.mark.parametrize(
    ("stat_type", "kwargs", "expected"),
    [
        (
            "batting",
            {"orderby": "runs", "qualifications": (Qualification(field="runs", minimum=1),)},
            "type=batting",
        ),
        (
            "bowling",
            {"orderby": "wickets", "qualifications": (Qualification(field="wickets", minimum=1),)},
            "type=bowling",
        ),
        (
            "fielding",
            {
                "orderby": "dismissals",
                "qualifications": (Qualification(field="dismissals", minimum=1),),
            },
            "type=fielding",
        ),
        (
            "allround",
            {
                "orderby": "allround_average",
                "qualifications": (Qualification(field="allround_average", minimum=1),),
            },
            "type=allround",
        ),
        (
            "fow",
            {
                "orderby": "fow_runs",
                "qualifications": (Qualification(field="fow_runs", minimum=1),),
            },
            "type=fow",
        ),
        (
            "team",
            {"orderby": "won", "qualifications": (Qualification(field="won", minimum=1),)},
            "type=team",
        ),
        (
            "aggregate",
            {"orderby": "runs", "qualifications": (Qualification(field="runs", minimum=1),)},
            "type=aggregate",
        ),
    ],
)
def test_every_stat_type_compiles_representative_query(
    stat_type: str, kwargs: dict[str, object], expected: str
) -> None:
    query = StatsguruQuery(**{"class": 3, "type": stat_type, **kwargs})

    url = query.results_url(as_of=date(2026, 10, 4))

    assert expected in url
    assert "spanmin1=17+Feb+2005" in url


def test_season_period_compiles_without_dates() -> None:
    query = StatsguruQuery(
        **{"class": 1, "type": "batting", "period": SeasonPeriod(season="2025/26")}
    )

    assert query.results_url(as_of=date(2026, 10, 4)).endswith(
        "?class=1;season=2025%2F26;template=results;type=batting"
    )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"class": 99, "type": "batting"}, "unknown class 99"),
        ({"class": 3, "type": "aggregate", "groupby": "team"}, "field 'groupby'"),
        (
            {"class": 3, "type": "aggregate", "innings_number": 1},
            "field 'innings_number' is not valid",
        ),
        ({"class": 3, "type": "batting", "team": 999999}, "unknown team ID 999999"),
        ({"class": 3, "type": "batting", "host": 999999}, "unknown host ID 999999"),
        ({"class": 3, "type": "batting", "continent": 999999}, "unknown continent ID 999999"),
        ({"class": 3, "type": "batting", "trophy": 999999}, "unknown trophy ID 999999"),
        ({"class": 3, "type": "batting", "result": 9}, "result value 9 is not valid"),
        (
            {"class": 3, "type": "batting", "runsmin1": 10, "runsmax1": 1},
            "runsmin1 must be <= runsmax1",
        ),
        (
            {"class": 3, "type": "batting", "batting_positionmin1": 13},
            "batting_positionmin1 must be from 0 to 12",
        ),
        (
            {
                "class": 3,
                "type": "batting",
                "view": "innings",
                "qualifications": (Qualification(field="runs", minimum=1),),
            },
            "qualification field 'runs' is not valid",
        ),
        (
            {"class": 3, "type": "batting", "orderby": "wickets"},
            "sort field 'wickets' is not valid",
        ),
    ],
)
def test_invalid_combinations_have_clear_messages(kwargs: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        StatsguruQuery(**kwargs)


def test_more_than_three_qualifications_is_rejected() -> None:
    with pytest.raises(ValidationError, match="at most three"):
        StatsguruQuery(
            **{
                "class": 3,
                "type": "batting",
                "qualifications": tuple(Qualification(field="runs", minimum=n) for n in range(4)),
            }
        )


def test_symbolic_period_refuses_unresolved_and_resolves_year_arithmetic() -> None:
    unresolved = StatsguruQuery(
        **{
            "class": 3,
            "type": "batting",
            "period": SymbolicPeriod(kind=SymbolicPeriodKind.LAST_YEARS, years=5),
        }
    )
    with pytest.raises(QuerySpecError, match="must be resolved"):
        unresolved.results_url()

    resolved = unresolved.resolved(first_match=date(2016, 9, 7), last_match=date(2026, 2, 24))
    assert "spanmin1=24+Feb+2021" in resolved.results_url()
    assert "spanmax1=24+Feb+2026" in resolved.results_url()

    first = SymbolicPeriod(kind=SymbolicPeriodKind.FIRST_YEARS, years=2).resolve(
        date(2020, 2, 29), date(2030, 1, 1)
    )
    last = SymbolicPeriod(kind=SymbolicPeriodKind.LAST_YEARS, years=1).resolve(
        date(2019, 1, 1), date(2024, 2, 29)
    )
    assert first == ResolvedPeriod(start=date(2020, 2, 29), end=date(2022, 2, 28))
    assert last == ResolvedPeriod(start=date(2023, 2, 28), end=date(2024, 2, 29))


def test_labels_use_plain_english_and_id_names() -> None:
    query = StatsguruQuery(
        **{
            "class": 3,
            "type": "batting",
            "team": 7,
            "period": ResolvedPeriod(start=date(2016, 9, 7), end=date(2026, 2, 24)),
            "qualifications": (
                Qualification(field="runs", minimum=1000),
                Qualification(field="batting_average", minimum=Decimal("38.94")),
                Qualification(field="batting_strike_rate", minimum=Decimal("128.02")),
            ),
            "orderby": "batting_average",
        }
    )

    assert query.label() == (
        "T20I batting, team Pakistan, at least 1000 runs, at least 38.94 average, "
        "at least 128.02 strike rate, 7 Sep 2016 to 24 Feb 2026, "
        "sorted by average descending"
    )


def test_player_search_url() -> None:
    assert player_search_url("babar azam") == (
        "https://stats.cricinfo.com/ci/engine/stats/analysis.html?"
        "search=babar+azam;template=analysis"
    )
