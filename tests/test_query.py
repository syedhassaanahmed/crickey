from __future__ import annotations

import asyncio
import importlib.util
import subprocess
import sys
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import pytest
from mcp import Client
from pydantic import ValidationError
from stat_type_cases import REPRESENTATIVE_STAT_QUERIES, representative_query_payload

from crickey import query_catalog
from crickey.fetcher import Fetcher, MemoryPageSource
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
from crickey.server import create_server
from crickey.settings import Settings

_GEN_QUERY_CATALOG_SPEC = importlib.util.spec_from_file_location(
    "gen_query_catalog_for_tests", Path(__file__).parents[1] / "scripts" / "gen_query_catalog.py"
)
assert _GEN_QUERY_CATALOG_SPEC is not None and _GEN_QUERY_CATALOG_SPEC.loader is not None
_GEN_QUERY_CATALOG_MODULE = importlib.util.module_from_spec(_GEN_QUERY_CATALOG_SPEC)
sys.modules[_GEN_QUERY_CATALOG_SPEC.name] = _GEN_QUERY_CATALOG_MODULE
_GEN_QUERY_CATALOG_SPEC.loader.exec_module(_GEN_QUERY_CATALOG_MODULE)
module_text_from_pages = _GEN_QUERY_CATALOG_MODULE.module_text_from_pages
CATALOG_CLASS_IDS = _GEN_QUERY_CATALOG_MODULE.CLASS_IDS
CATALOG_STAT_TYPES = _GEN_QUERY_CATALOG_MODULE.STAT_TYPES
TYPE_LABELS_FOR_TESTS = {
    "allround": "all-round",
    "bowling": "bowling",
    "fielding": "fielding",
}
FIRST_MATCH_DATES = {
    1: "15 Mar 1877",
    2: "05 Jan 1971",
    3: "17 Feb 2005",
    6: "13 Jun 2003",
    11: "15 Mar 1877",
}

R5_OVERALL_QUAL_FIELDS = {
    "batting": {
        "matches",
        "innings",
        "notouts",
        "outs",
        "runs",
        "minutes",
        "balls_faced",
        "batting_average",
        "batting_strike_rate",
        "hundreds",
        "fifty_plus",
        "ducks",
        "fours",
        "sixes",
    },
    "bowling": {
        "matches",
        "innings_bowled",
        "balls",
        "overs",
        "maidens",
        "conceded",
        "wickets",
        "bowling_average",
        "economy_rate",
        "bowling_strike_rate",
        "four_plus_wickets",
        "five_wickets",
        "ten_wickets",
    },
    "fielding": {
        "matches",
        "matches_keeper",
        "matches_fielder",
        "innings_fielded",
        "dismissals",
        "caught",
        "stumped",
        "caught_keeper",
        "caught_fielder",
        "dismissals_per_inns",
    },
    "allround": {
        "matches",
        "innings",
        "notouts",
        "outs",
        "runs",
        "minutes",
        "balls_faced",
        "batting_average",
        "batting_strike_rate",
        "hundreds",
        "fifty_plus",
        "ducks",
        "fours",
        "sixes",
        "innings_bowled",
        "balls",
        "maidens",
        "conceded",
        "wickets",
        "bowling_average",
        "economy_rate",
        "bowling_strike_rate",
        "four_plus_wickets",
        "five_wickets",
        "ten_wickets",
        "matches_keeper",
        "matches_fielder",
        "innings_fielded",
        "dismissals",
        "caught",
        "stumped",
        "caught_keeper",
        "caught_fielder",
        "dismissals_per_inns",
        "allround_average",
    },
    "fow": {
        "fow_innings",
        "fow_notouts",
        "fow_outs",
        "fow_runs",
        "fow_average",
        "fow_balls_faced",
        "fow_run_rate",
        "fow_hundreds",
        "fow_fifty_plus",
    },
    "team": {
        "matches",
        "won",
        "lost",
        "tied",
        "drawn",
        "no_result",
        "win_loss_ratio",
        "percentage_won",
        "percentage_lost",
        "percentage_drawn",
        "percentage_tied",
        "percentage_no_result",
        "runs",
        "wickets",
        "balls",
        "team_average",
        "runs_per_over",
        "team_innings",
        "team_high_score",
        "team_low_score",
    },
    "aggregate": {
        "matches",
        "won",
        "tied",
        "drawn",
        "no_result",
        "percentage_won",
        "percentage_lost",
        "percentage_drawn",
        "percentage_tied",
        "percentage_no_result",
        "runs",
        "wickets",
        "balls",
        "team_average",
        "runs_per_over",
    },
}
R5_OVERALL_SORT_FIELDS = {
    "batting": R5_OVERALL_QUAL_FIELDS["batting"] | {"player", "start", "high_score"},
    "bowling": R5_OVERALL_QUAL_FIELDS["bowling"] | {"player", "start", "bbi", "bbm"},
    "fielding": R5_OVERALL_QUAL_FIELDS["fielding"] | {"player", "start", "age", "max_dismissals"},
    "allround": R5_OVERALL_QUAL_FIELDS["allround"]
    | {"player", "start", "high_score", "bbi", "bbm", "max_dismissals"},
    "fow": R5_OVERALL_QUAL_FIELDS["fow"] | {"partners", "start", "fow_high_score"},
    "team": R5_OVERALL_QUAL_FIELDS["team"] | {"team", "start"},
    "aggregate": R5_OVERALL_QUAL_FIELDS["aggregate"] | {"start"},
}


class FrozenClock:
    def now(self):
        from datetime import UTC, datetime

        return datetime(2026, 10, 4, 12, 0, tzinfo=UTC)

    def monotonic(self) -> float:
        return 0.0

    async def sleep(self, seconds: float) -> None:
        return None


def _query_pairs(url: str) -> dict[str, list[str]]:
    values: dict[str, list[str]] = defaultdict(list)
    for key, value in parse_qsl(urlsplit(url).query, separator=";"):
        values[key].append(value)
    return dict(values)


def _first_match_date(class_id: int) -> str:
    return FIRST_MATCH_DATES[class_id]


async def _query_stats_fetch_false(query: dict[str, object]) -> dict[str, object]:
    fetcher = Fetcher(Settings(), clock=FrozenClock(), page_source=MemoryPageSource({}))
    client = Client(create_server(Settings(), fetcher=fetcher))
    async with client:
        result = await client.call_tool("query_stats", {"query": query, "fetch": False})
    assert result.is_error is False
    return result.structured_content


async def _query_stats_fetch_false_many(queries: list[dict[str, object]]) -> None:
    fetcher = Fetcher(Settings(), clock=FrozenClock(), page_source=MemoryPageSource({}))
    async with Client(create_server(Settings(), fetcher=fetcher)) as client:
        for query in queries:
            result = await client.call_tool("query_stats", {"query": query, "fetch": False})
            assert result.is_error is False, query


def _synthetic_advanced_form(stat_type: str, *, result_values: str = "") -> str:
    result_values = result_values or (
        '<input type="checkbox" name="result" value="1"> won match'
        '<input type="checkbox" name="result" value="5"> no result'
    )
    return f"""
    <html><body><form name="gurumenu">
      <input type="hidden" name="spanmin0" value="01 Jan 2000">
      <input type="hidden" name="spanmax0" value="31 Dec 2026">
      <input type="hidden" name="runsmin0" value="0">
      <input type="hidden" name="runsmax0" value="999">
      <input type="hidden" name="runsval1" value="runs">
      <input type="radio" name="view" value=""> Overall figures
      <input type="radio" name="view" value="innings"> Innings by innings list
      <select name="groupby"><option value="">individual players</option>
        <option value="innings">each team innings</option></select>
      {result_values}
      <input type="radio" name="captain" value="1"> as captain
      <input type="radio" name="captain" value="0"> not as captain
      <select name="qualquickpick"><option value="0">- quick pick -</option>
        <option value="1">0 only</option></select>
      <select name="runsquickpick"><option value="1">no runs</option></select>
      <input type="text" name="runsmin1" value="0">
      <input type="text" name="runsmax1" value="999">
      <select id="havingselect_{stat_type}_default" name="qualval1">
        <option value="">none</option><option value="runs">runs</option></select>
      <select id="havingselect_{stat_type}_innings" name="qualval1">
        <option value="">none</option><option value="batted_score">runs scored</option></select>
      <select id="orderbyselect_{stat_type}_default" name="orderby">
        <option value="runs">runs</option></select>
      <select id="orderbyselect_{stat_type}_innings" name="orderby">
        <option value="start">start date</option></select>
    </form></body></html>
    """


def _synthetic_player_form() -> str:
    return """
    <html><body><form name="gurumenu">
      <input type="radio" name="view" value=""> Career summary
      <input type="radio" name="view" value="innings"> Innings by innings list
      <input type="radio" name="view" value="cumulative"> Cumulative averages
      <input type="checkbox" name="result" value="1"> won match
      <input type="checkbox" name="result" value="5"> no result
    </form></body></html>
    """


def test_query_catalog_generator_builds_exact_tables_from_synthetic_forms() -> None:
    advanced = {
        (class_id, stat_type): _synthetic_advanced_form(stat_type)
        for class_id in CATALOG_CLASS_IDS
        for stat_type in CATALOG_STAT_TYPES
    }
    players = {class_id: _synthetic_player_form() for class_id in CATALOG_CLASS_IDS}

    text = module_text_from_pages(advanced, players)
    expected = (Path(__file__).parent / "fixtures" / "query_catalog_expected.py.txt").read_text(
        encoding="utf-8"
    )

    assert text == expected

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "format",
            "--check",
            "--stdin-filename",
            "src/crickey/query_catalog.py",
            "-",
        ],
        input=text,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


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


def test_player_page_view_is_validated_and_compiled_exactly() -> None:
    page = PlayerPageSpec(
        player_id=348144,
        **{
            "class": 3,
            "type": "batting",
            "view": "innings",
            "result": [2, 1],
        },
    )

    assert page.url(as_of=date(2026, 10, 4)) == (
        "https://stats.cricinfo.com/ci/engine/player/348144.html?"
        "class=3;result=1;result=2;spanmax1=04+Oct+2026;spanmin1=17+Feb+2005;"
        "spanval1=span;template=results;type=batting;view=innings"
    )
    with pytest.raises(ValidationError, match="view 'bowler_summary' is not valid"):
        PlayerPageSpec(
            player_id=348144, **{"class": 3, "type": "bowling", "view": "bowler_summary"}
        )


def test_player_page_uses_class_choices_not_one_players_form_lists() -> None:
    test_page = PlayerPageSpec(player_id=253802, **{"class": 1, "type": "batting", "result": 3})
    t20_page = PlayerPageSpec(
        player_id=253802, **{"class": 6, "type": "batting", "home_or_away": 4}
    )
    other_player = PlayerPageSpec(
        player_id=253802,
        **{
            "class": 2,
            "type": "batting",
            "opposition": 32,
            "season": "2010",
            "home_or_away": 2,
        },
    )

    assert "result=3" in test_page.url(as_of=date(2026, 10, 4))
    assert "home_or_away=4" in t20_page.url(as_of=date(2026, 10, 4))
    assert "opposition=32" in other_player.url(as_of=date(2026, 10, 4))


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


def test_raw_qualification_fields_round_trip_after_merge() -> None:
    query = StatsguruQuery(
        **{
            "class": 2,
            "type": "batting",
            "qualval1": "hundreds",
            "qualmin1": 10,
            "qualval2": "runs",
            "qualmax2": 10000,
        }
    )

    assert query.qualifications == (
        Qualification(field="hundreds", minimum=10),
        Qualification(field="runs", maximum=10000),
    )
    assert query.qualval1 is None
    assert query.qualmin1 is None

    reparsed = StatsguruQuery.model_validate(query.model_dump(by_alias=True))

    assert reparsed == query


def test_alphabetical_repeated_keys_are_sorted_and_date_edge_formatting() -> None:
    query = StatsguruQuery(
        **{
            "class": 3,
            "type": "team",
            "result": [2, 1],
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


@pytest.mark.parametrize("class_id", CATALOG_CLASS_IDS)
@pytest.mark.parametrize("stat_type", CATALOG_STAT_TYPES)
def test_every_stat_type_and_class_compiles_representative_query(
    class_id: int, stat_type: str
) -> None:
    case = REPRESENTATIVE_STAT_QUERIES[stat_type]
    payload = asyncio.run(
        _query_stats_fetch_false(representative_query_payload(class_id, stat_type))
    )
    query = _query_pairs(str(payload["link"]))

    assert query == {
        "class": [str(class_id)],
        "type": [stat_type],
        "template": ["results"],
        "orderby": [case.orderby],
        "qualval1": [case.qualification],
        "qualmin1": [str(case.qualification_minimum)],
        "size": ["10"],
        "spanmin1": [_first_match_date(class_id)],
        "spanmax1": ["04 Oct 2026"],
        "spanval1": ["span"],
        **{key: [value] for key, value in case.expected_filter_params.items()},
    }


@pytest.mark.parametrize(
    ("stat_type", "case_kind", "kwargs", "message"),
    [
        (
            "batting",
            "minimum",
            {"qualifications": (Qualification(field="wickets", minimum=1),)},
            "qualification field 'wickets' is not valid",
        ),
        ("batting", "sort", {"orderby": "wickets"}, "sort field 'wickets' is not valid"),
        ("batting", "field", {"wicketsmin1": 1}, "field 'wicketsmin1' is not valid"),
        (
            "bowling",
            "minimum",
            {"qualifications": (Qualification(field="hundreds", minimum=1),)},
            "qualification field 'hundreds' is not valid",
        ),
        (
            "bowling",
            "sort",
            {"orderby": "hundreds"},
            "sort field 'hundreds' is not valid",
        ),
        ("bowling", "field", {"runsmin1": 1}, "field 'runsmin1' is not valid"),
        (
            "fielding",
            "minimum",
            {"qualifications": (Qualification(field="runs", minimum=1),)},
            "qualification field 'runs' is not valid",
        ),
        (
            "fielding",
            "sort",
            {"orderby": "high_score"},
            "sort field 'high_score' is not valid",
        ),
        ("fielding", "field", {"wicketsmin1": 1}, "field 'wicketsmin1' is not valid"),
        (
            "allround",
            "minimum",
            {"qualifications": (Qualification(field="fow_runs", minimum=1),)},
            "qualification field 'fow_runs' is not valid",
        ),
        ("allround", "sort", {"orderby": "fow_runs"}, "sort field 'fow_runs' is not valid"),
        (
            "allround",
            "field",
            {"partnership_runsmin1": 1},
            "field 'partnership_runsmin1' is not valid",
        ),
        (
            "fow",
            "minimum",
            {"qualifications": (Qualification(field="runs", minimum=1),)},
            "qualification field 'runs' is not valid",
        ),
        ("fow", "sort", {"orderby": "player"}, "sort field 'player' is not valid"),
        ("fow", "field", {"captain": 1}, "field 'captain' is not valid"),
        (
            "team",
            "minimum",
            {"qualifications": (Qualification(field="allround_average", minimum=1),)},
            "qualification field 'allround_average' is not valid",
        ),
        ("team", "sort", {"orderby": "player"}, "sort field 'player' is not valid"),
        ("team", "field", {"agemin1": 20}, "field 'agemin1' is not valid"),
        (
            "aggregate",
            "minimum",
            {"qualifications": (Qualification(field="lost", minimum=1),)},
            "qualification field 'lost' is not valid",
        ),
        ("aggregate", "sort", {"orderby": "team"}, "sort field 'team' is not valid"),
        ("aggregate", "field", {"opposition": 7}, "field 'opposition' is not valid"),
    ],
)
def test_stat_types_reject_other_types_minimums_sorts_and_fields(
    stat_type: str, case_kind: str, kwargs: dict[str, object], message: str
) -> None:
    assert case_kind in {"minimum", "sort", "field"}
    with pytest.raises(ValidationError, match=message):
        StatsguruQuery(**{"class": 1, "type": stat_type, **kwargs})


@pytest.mark.parametrize("class_id", CATALOG_CLASS_IDS)
@pytest.mark.parametrize("stat_type", CATALOG_STAT_TYPES)
def test_catalog_has_r5_overall_minimum_fields(class_id: int, stat_type: str) -> None:
    assert (
        set(query_catalog.QUAL_FIELDS[class_id][stat_type][""]) == R5_OVERALL_QUAL_FIELDS[stat_type]
    )


@pytest.mark.parametrize("class_id", CATALOG_CLASS_IDS)
@pytest.mark.parametrize("stat_type", CATALOG_STAT_TYPES)
def test_catalog_has_r5_overall_sort_fields(class_id: int, stat_type: str) -> None:
    assert (
        set(query_catalog.SORT_FIELDS[class_id][stat_type][""]) == R5_OVERALL_SORT_FIELDS[stat_type]
    )


@pytest.mark.parametrize("class_id", CATALOG_CLASS_IDS)
@pytest.mark.parametrize("stat_type", CATALOG_STAT_TYPES)
def test_query_stats_accepts_every_r5_overall_minimum_field(class_id: int, stat_type: str) -> None:
    asyncio.run(
        _query_stats_fetch_false_many(
            [
                {"class": class_id, "type": stat_type, "qualval1": field, "qualmin1": 1}
                for field in sorted(R5_OVERALL_QUAL_FIELDS[stat_type])
            ]
        )
    )


@pytest.mark.parametrize("class_id", CATALOG_CLASS_IDS)
@pytest.mark.parametrize("stat_type", CATALOG_STAT_TYPES)
def test_query_stats_accepts_every_r5_overall_sort_field(class_id: int, stat_type: str) -> None:
    asyncio.run(
        _query_stats_fetch_false_many(
            [
                {"class": class_id, "type": stat_type, "orderby": field}
                for field in sorted(R5_OVERALL_SORT_FIELDS[stat_type])
            ]
        )
    )


def test_season_period_compiles_without_dates() -> None:
    query = StatsguruQuery(
        **{"class": 1, "type": "batting", "period": SeasonPeriod(season="2025/26")}
    )

    assert query.results_url(as_of=date(2026, 10, 4)) == (
        "https://stats.cricinfo.com/ci/engine/stats/index.html?"
        "class=1;season=2025%2F26;spanmax1=04+Oct+2026;spanmin1=15+Mar+1877;"
        "spanval1=span;template=results;type=batting"
    )


def test_view_groupby_orderbyad_page_and_qualmax_are_in_exact_url() -> None:
    query = StatsguruQuery(
        **{
            "class": 1,
            "type": "batting",
            "view": "innings",
            "groupby": "innings",
            "qualifications": (Qualification(field="batted_score", minimum=50, maximum=100),),
            "orderby": "start",
            "orderbyad": "reverse",
            "page": 2,
            "size": 10,
        }
    )

    assert query.results_url(as_of=date(2026, 10, 4)) == (
        "https://stats.cricinfo.com/ci/engine/stats/index.html?"
        "class=1;groupby=innings;orderby=start;orderbyad=reverse;page=2;"
        "qualmax1=100;qualmin1=50;qualval1=batted_score;size=10;"
        "spanmax1=04+Oct+2026;spanmin1=15+Mar+1877;spanval1=span;"
        "template=results;type=batting;view=innings"
    )


def test_page_one_is_omitted() -> None:
    query = StatsguruQuery(**{"class": 1, "type": "batting", "page": 1})

    assert "page=1" not in query.results_url(as_of=date(2026, 10, 4))


def test_range_filters_emit_matching_val1_and_quickpicks_are_refused() -> None:
    query = StatsguruQuery(**{"class": 1, "type": "batting", "runsmin1": 100})

    assert query.results_url(as_of=date(2026, 10, 4)) == (
        "https://stats.cricinfo.com/ci/engine/stats/index.html?"
        "class=1;runsmin1=100;runsval1=runs;spanmax1=04+Oct+2026;"
        "spanmin1=15+Mar+1877;spanval1=span;template=results;type=batting"
    )
    with pytest.raises(ValidationError, match="runsquickpick is not supported; use min/max"):
        StatsguruQuery(**{"class": 1, "type": "batting", "runsquickpick": 8})
    with pytest.raises(ValidationError, match="spanquickpick is not supported; use period"):
        StatsguruQuery(**{"class": 1, "type": "batting", "spanquickpick": 5})


def test_multi_value_fields_accept_lists_and_sort_repeated_keys() -> None:
    with pytest.raises(ValidationError, match="field 'toss' accepts only one value"):
        StatsguruQuery(**{"class": 1, "type": "batting", "toss": [1, 2]})

    team_query = StatsguruQuery(**{"class": 1, "type": "batting", "team": [2, 1]})
    assert "team=1;team=2" in team_query.results_url(as_of=date(2026, 10, 4))

    query = StatsguruQuery(**{"class": 1, "type": "batting", "result": [2, 1]})
    assert "result=1;result=2" in query.results_url(as_of=date(2026, 10, 4))


def test_per_view_qualification_and_sort_tables_match_forms() -> None:
    team_innings = StatsguruQuery(
        **{
            "class": 1,
            "type": "team",
            "view": "innings",
            "qualifications": (Qualification(field="team_score", minimum=100),),
            "orderby": "team_score",
        }
    )
    assert "qualval1=team_score" in team_innings.results_url(as_of=date(2026, 10, 4))
    with pytest.raises(ValidationError, match="qualification field 'won' is not valid"):
        StatsguruQuery(
            **{
                "class": 1,
                "type": "team",
                "view": "innings",
                "qualifications": (Qualification(field="won", minimum=1),),
            }
        )
    assert "qualval1=awards_match" in StatsguruQuery(
        **{
            "class": 1,
            "type": "allround",
            "view": "awards",
            "qualifications": (Qualification(field="awards_match", minimum=1),),
        }
    ).results_url(as_of=date(2026, 10, 4))
    assert "qualval1=fow_score" in StatsguruQuery(
        **{
            "class": 1,
            "type": "fow",
            "view": "innings",
            "qualifications": (Qualification(field="fow_score", minimum=100),),
        }
    ).results_url(as_of=date(2026, 10, 4))
    assert "qualval1=year" in StatsguruQuery(
        **{
            "class": 1,
            "type": "aggregate",
            "view": "year",
            "qualifications": (Qualification(field="year", minimum=2020),),
        }
    ).results_url(as_of=date(2026, 10, 4))


def test_per_class_catalog_differences_are_validated() -> None:
    assert "result=5" in StatsguruQuery(**{"class": 2, "type": "batting", "result": 5}).results_url(
        as_of=date(2026, 10, 4)
    )
    assert "final_type=3" in StatsguruQuery(
        **{"class": 2, "type": "batting", "final_type": 3}
    ).results_url(as_of=date(2026, 10, 4))
    assert "agemin1=55" in StatsguruQuery(
        **{"class": 3, "type": "batting", "agemin1": 55}
    ).results_url(as_of=date(2026, 10, 4))
    assert "floodlit=3" in StatsguruQuery(
        **{"class": 3, "type": "batting", "floodlit": 3}
    ).results_url(as_of=date(2026, 10, 4))
    assert "dismissal=9" in StatsguruQuery(
        **{"class": 3, "type": "batting", "dismissal": 9}
    ).results_url(as_of=date(2026, 10, 4))

    invalid_cases = (
        ({"class": 2, "type": "batting", "result": 4}, "result value 4 is not valid"),
        ({"class": 2, "type": "batting", "innings_number": 3}, "innings_number value 3"),
        ({"class": 2, "type": "batting", "view": "match"}, "view 'match' is not valid"),
        ({"class": 1, "type": "batting", "agemin1": 55}, "agemin1 must be from 14 to 52"),
    )
    for kwargs, message in invalid_cases:
        with pytest.raises(ValidationError, match=message):
            StatsguruQuery(**kwargs)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"class": 99, "type": "batting"}, "unknown class 99"),
        ({"class": 3, "type": "aggregate", "groupby": "team"}, "groupby 'team' is not valid"),
        (
            {"class": 3, "type": "aggregate", "innings_number": 1},
            "field 'innings_number' is not valid",
        ),
        ({"class": 3, "type": "batting", "team": 999999}, "unknown team ID 999999"),
        ({"class": 3, "type": "batting", "team": "Pakistan"}, "team ID 'Pakistan' is not a number"),
        ({"class": 3, "type": "batting", "opposition": 999999}, "unknown opposition ID 999999"),
        ({"class": 3, "type": "batting", "host": 999999}, "unknown host ID 999999"),
        ({"class": 3, "type": "batting", "continent": 999999}, "unknown continent ID 999999"),
        ({"class": 3, "type": "batting", "trophy": 999999}, "unknown trophy ID 999999"),
        ({"class": 3, "type": "batting", "result": 9}, "result value 9 is not valid"),
        (
            {"class": 3, "type": "batting", "search_player": "babar azam"},
            "search_player must be resolved to player_involve IDs first",
        ),
        (
            {"class": 3, "type": "batting", "search_captain": "babar azam"},
            "search_captain must be resolved to captain_involve IDs first",
        ),
        (
            {"class": 3, "type": "batting", "runsmin1": 10, "runsmax1": 1},
            "runsmin1 must be <= runsmax1",
        ),
        (
            {"class": 3, "type": "batting", "batting_positionmin1": 13},
            "batting_positionmin1 must be from 0 to 12",
        ),
        ({"class": 3, "type": "batting", "season": "World Cup"}, "season value 'World Cup'"),
        (
            {"class": 3, "type": "aggregate", "opposition": 7},
            "field 'opposition' is not valid",
        ),
        (
            {"class": 3, "type": "team", "agemin1": 20},
            "field 'agemin1' is not valid",
        ),
        (
            {"class": 3, "type": "fow", "debut_or_last": 1},
            "field 'debut_or_last' is not valid",
        ),
        ({"class": 3, "type": "fow", "captain": 1}, "field 'captain' is not valid"),
        ({"class": 3, "type": "fow", "keeper": 1}, "field 'keeper' is not valid"),
        ({"class": 3, "type": "batting", "final_type": 99}, "final_type value 99"),
        ({"class": 3, "type": "batting", "batting_hand": 99}, "batting_hand value 99"),
        ({"class": 3, "type": "batting", "outs": 2}, "outs value 2"),
        ({"class": 3, "type": "batting", "dismissal": 99}, "dismissal value 99"),
        ({"class": 3, "type": "team", "event": 99}, "event value 99"),
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


def test_aggregate_accepts_captain_involve_from_form_search_flow() -> None:
    query = StatsguruQuery(
        **{
            "class": 3,
            "type": "aggregate",
            "search_captain": "babar azam",
            "captain_involve": [56880],
            "captain_involve_type": "none",
        }
    )

    assert query.results_url(as_of=date(2026, 10, 4)) == (
        "https://stats.cricinfo.com/ci/engine/stats/index.html?"
        "captain_involve=56880;captain_involve_type=none;class=3;"
        "spanmax1=04+Oct+2026;spanmin1=17+Feb+2005;spanval1=span;"
        "template=results;type=aggregate"
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
    with pytest.raises(QuerySpecError, match="must be resolved"):
        PlayerPageSpec(
            player_id=348144,
            **{
                "class": 3,
                "type": "batting",
                "period": SymbolicPeriod(kind=SymbolicPeriodKind.LAST_YEARS, years=5),
            },
        ).label()

    career = StatsguruQuery(
        **{
            "class": 3,
            "type": "batting",
            "period": SymbolicPeriod(kind=SymbolicPeriodKind.CAREER),
        }
    ).resolved(first_match=date(2016, 9, 7), last_match=date(2026, 2, 24))
    assert "spanmin1=07+Sep+2016" in career.results_url()
    assert "spanmax1=24+Feb+2026" in career.results_url()

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
    clamped = SymbolicPeriod(kind=SymbolicPeriodKind.FIRST_YEARS, years=5).resolve(
        date(2024, 3, 1), date(2026, 2, 24)
    )
    assert clamped == ResolvedPeriod(start=date(2024, 3, 1), end=date(2026, 2, 24))


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
        "sorted by average"
    )


def test_labels_describe_view_groupby_ranges_choices_and_sort_direction() -> None:
    query = StatsguruQuery(
        **{
            "class": 1,
            "type": "batting",
            "view": "innings",
            "groupby": "innings",
            "runsmin1": 100,
            "captain": 1,
            "result": [2, 1],
            "orderby": "start",
        }
    )

    assert query.label(as_of=date(2026, 10, 4)) == (
        "Test batting, view innings, grouped by innings, captain as captain, "
        "result won match or lost match, at least 100 runs in an innings, "
        "15 Mar 1877 to 4 Oct 2026, sorted by start date ascending"
    )


def test_labels_include_season_filter_and_do_not_duplicate_innings_number() -> None:
    query = StatsguruQuery(
        **{"class": 1, "type": "batting", "season": "2019/20", "innings_number": 1}
    )

    assert query.label(as_of=date(2026, 10, 4)) == (
        "Test batting, innings number 1st innings, season 2019/20, 15 Mar 1877 to 4 Oct 2026"
    )


def test_unverified_sort_fields_have_no_direction_in_labels() -> None:
    query = StatsguruQuery(**{"class": 1, "type": "batting", "orderby": "player"})

    assert query.label(as_of=date(2026, 10, 4)) == (
        "Test batting, 15 Mar 1877 to 4 Oct 2026, sorted by player"
    )


def test_player_page_label_describes_parameters() -> None:
    page = PlayerPageSpec(
        player_id=348144,
        **{
            "class": 2,
            "type": "batting",
            "view": "innings",
            "trophy": 12,
            "period": SeasonPeriod(season="2023"),
        },
    )

    assert page.label(as_of=date(2026, 10, 4)) == (
        "ODI player batting for 348144, view innings, trophy World Cup, season 2023"
    )


@pytest.mark.parametrize("stat_type", ["allround", "bowling", "fielding"])
def test_player_page_labels_use_choice_labels_for_each_type(stat_type: str) -> None:
    page = PlayerPageSpec(
        player_id=348144,
        **{"class": 3, "type": stat_type, "result": 5, "home_or_away": 3},
    )

    assert page.label(as_of=date(2026, 10, 4)) == (
        f"T20I player {TYPE_LABELS_FOR_TESTS[stat_type]} for 348144, "
        "home or away neutral venue, result no result, 17 Feb 2005 to 4 Oct 2026"
    )


def test_player_page_rejects_drawn_result_and_accepts_no_result() -> None:
    with pytest.raises(ValidationError, match="result value 4 is not valid"):
        PlayerPageSpec(player_id=348144, **{"class": 2, "type": "batting", "result": 4})
    assert "result=5" in PlayerPageSpec(
        player_id=348144, **{"class": 2, "type": "batting", "result": 5}
    ).url(as_of=date(2026, 10, 4))


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"opposition": [7, 8]}, "field 'opposition' accepts only one value"),
        ({"spanquickpick": 5}, "spanquickpick is not supported; use period"),
        ({"opposition": 999999}, "unknown opposition ID 999999"),
        ({"season": "World Cup"}, "season value 'World Cup'"),
    ],
)
def test_player_page_invalid_filters_have_clear_messages(
    kwargs: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        PlayerPageSpec(player_id=348144, **{"class": 3, "type": "batting", **kwargs})


def test_labels_include_choice_names_and_exclusions_exactly() -> None:
    query = StatsguruQuery(
        **{
            "class": 3,
            "type": "batting",
            "dismissal": 3,
            "outs": 1,
            "ground": 198,
            "player_involve": 56880,
            "player_involve_type": "none",
        }
    )

    assert query.label(as_of=date(2026, 10, 4)) == (
        "T20I batting, dismissal leg before wicket, outs out, excluding player involve 56880, "
        "ground 198, 17 Feb 2005 to 4 Oct 2026"
    )


def test_player_search_url() -> None:
    assert player_search_url("babar azam") == (
        "https://stats.cricinfo.com/ci/engine/stats/analysis.html?"
        "search=babar+azam;template=analysis"
    )
