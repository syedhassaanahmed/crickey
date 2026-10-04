from __future__ import annotations

# ruff: noqa: E501
import asyncio
from datetime import date, timedelta
from decimal import Decimal

from crickey.fetcher import Fetcher, MemoryPageSource
from crickey.metrics import BATTING_METRICS, BetterDirection, batting_metric
from crickey.parsers import RecentMatch
from crickey.proof import Threshold, build_proof_link
from crickey.query import (
    Qualification,
    ResolvedPeriod,
    StatsguruQuery,
    SymbolicPeriod,
    SymbolicPeriodKind,
)
from crickey.render import (
    AnswerRenderInput,
    RenderedTable,
    RenderPlayer,
    freshness_line,
    render_answer,
)
from crickey.resolve import NameResolver, PeriodResolver, ResolveStatus
from crickey.settings import Settings


def test_derived_batting_formula_values_and_zero_denominators() -> None:
    row = {"Inns": 140, "100": 20, "50": 58, "BF": 7652, "NO": 16}

    assert batting_metric("innings_per_hundred").value_from_row(row) == Decimal("7")
    assert batting_metric("innings_per_fifty_plus").value_from_row(row) == Decimal(140) / Decimal(
        58
    )
    assert batting_metric("balls_per_dismissal").value_from_row(row) == Decimal(7652) / Decimal(124)
    assert batting_metric("innings_per_hundred").value_from_row({"Inns": 10, "100": 0}) is None
    assert (
        batting_metric("balls_per_dismissal").value_from_row({"BF": 10, "Inns": 2, "NO": 2}) is None
    )


def test_metric_direction_default_minimums_and_displayed_ties() -> None:
    avg = batting_metric("average")
    iph = batting_metric("innings_per_hundred")

    assert avg.direction == BetterDirection.HIGHER
    assert iph.direction == BetterDirection.LOWER
    assert avg.better_than(Decimal("50.01"), Decimal("50.00")) is True
    assert iph.better_than(Decimal("6.9"), Decimal("7.0")) is True
    assert avg.tied(Decimal("38.94"), Decimal("38.94")) is True
    assert avg.default_minimum(3).field == "innings"
    assert avg.default_minimum(3).minimum == 20
    assert {
        metric.default_minimum(class_id).field
        for metric in BATTING_METRICS.values()
        for class_id in (1, 2, 3, 6, 11)
    }


def test_player_resolution_unique_ambiguous_unknown_officials_country_and_format_filters() -> None:
    async def run() -> None:
        search_url = "https://stats.cricinfo.com/ci/engine/stats/analysis.html?search=babar;template=analysis"
        html = """
        <table>
        <tr><td>Babar Azam</td><td>PAK</td><td><a href="/ci/engine/player/348144.html?class=3;type=allround">Twenty20 Internationals player</a> (2016 - 2026, 145 matches)</td></tr>
        <tr><td>Babar Hayat</td><td>HKG</td><td><a href="/ci/engine/player/539305.html?class=3;type=allround">Twenty20 Internationals player</a> (2014 - 2026, 79 matches)</td></tr>
        <tr><td>Babar Official</td><td>PAK</td><td><a href="/ci/engine/player/1.html?class=3;type=allround">Twenty20 Internationals official</a> (2020 - 2021)</td></tr>
        <tr><td>Other Babar</td><td>PAK</td><td><a href="/ci/engine/player/2.html?class=1;type=allround">Test matches player</a> (2020 - 2021, 1 match)</td></tr>
        </table>
        """
        source = MemoryPageSource(
            {
                search_url: html,
                "https://stats.cricinfo.com/ci/engine/stats/analysis.html?search=Babar;template=analysis": html,
                "https://stats.cricinfo.com/ci/engine/stats/analysis.html?search=Babar+Azam;template=analysis": html,
                "https://stats.cricinfo.com/ci/engine/stats/analysis.html?search=No+Such;template=analysis": "<html></html>",
            }
        )
        resolver = NameResolver(
            Fetcher(Settings(min_interval=timedelta(seconds=0)), page_source=source)
        )

        unique = await resolver.resolve_player("Babar Azam", class_id=3)
        ambiguous = await resolver.resolve_player("Babar", class_id=3)
        country = await resolver.resolve_player("Babar", class_id=3, country="HKG")
        unknown = await resolver.resolve_player("No Such", class_id=3)

        assert unique.status == ResolveStatus.MATCH
        assert unique.match is not None and unique.match.player_id == 348144
        assert ambiguous.needs_clarification is True
        assert [candidate.name for candidate in ambiguous.candidates] == [
            "Babar Azam",
            "Babar Hayat",
        ]
        assert country.match is not None and country.match.player_id == 539305
        assert unknown.needs_clarification is True and unknown.candidates == ()

    asyncio.run(run())


def test_team_ground_and_trophy_resolution() -> None:
    async def run() -> None:
        form_url = "https://stats.cricinfo.com/ci/engine/stats/index.html?class=1;filter=advanced;type=batting"
        form = """
        <form name="gurumenu">
        <select name="ground"><option value="131">AUS: Adelaide Oval</option><option value="132">ENG: Lord's</option></select>
        </form>
        """
        source = MemoryPageSource({form_url: form})
        resolver = NameResolver(
            Fetcher(Settings(min_interval=timedelta(seconds=0)), page_source=source)
        )

        assert resolver.resolve_team("India", class_id=1).match.value == 6
        assert resolver.resolve_trophy("Ashes", class_id=1).match.value == 1
        ground = await resolver.resolve_ground("Lord's", class_id=1)
        assert ground.match is not None and ground.match.value == 132

    asyncio.run(run())


def test_career_span_and_first_last_year_periods_from_player_form() -> None:
    async def run() -> None:
        form_url = "https://stats.cricinfo.com/ci/engine/player/348144.html?class=3;type=batting"
        form = """
        <form name="gurumenu"><input type="hidden" name="spanmin0" value="07 Sep 2016"><input type="hidden" name="spanmax0" value="29 Feb 2024"></form>
        """
        source = MemoryPageSource({form_url: form})
        resolver = PeriodResolver(
            Fetcher(Settings(min_interval=timedelta(seconds=0)), page_source=source)
        )

        career = await resolver.career_span(348144, class_id=3)
        first = await resolver.resolve_symbolic(
            348144,
            class_id=3,
            period=SymbolicPeriod(kind=SymbolicPeriodKind.FIRST_YEARS, years=2),
        )
        last = await resolver.resolve_symbolic(
            348144,
            class_id=3,
            period=SymbolicPeriod(kind=SymbolicPeriodKind.LAST_YEARS, years=1),
        )

        assert career == ResolvedPeriod(start=date(2016, 9, 7), end=date(2024, 2, 29))
        assert first == ResolvedPeriod(start=date(2016, 9, 7), end=date(2018, 9, 7))
        assert last == ResolvedPeriod(start=date(2023, 2, 28), end=date(2024, 2, 29))

    asyncio.run(run())


def test_proof_link_adds_qualval2_and_qualval3_and_fetches_once_to_confirm() -> None:
    async def run() -> None:
        query = StatsguruQuery(
            **{
                "class": 3,
                "type": "batting",
                "period": ResolvedPeriod(start=date(2016, 9, 7), end=date(2026, 2, 24)),
                "qualifications": (Qualification(field="runs", minimum=1000),),
                "orderby": "batting_average",
                "size": 200,
            }
        )
        expected_url = (
            "https://stats.cricinfo.com/ci/engine/stats/index.html?"
            "class=3;orderby=batting_average;qualmin1=1000;qualmin2=38.94;qualmin3=128.02;"
            "qualval1=runs;qualval2=batting_average;qualval3=batting_strike_rate;size=200;"
            "spanmax1=24+Feb+2026;spanmin1=07+Sep+2016;spanval1=span;template=results;type=batting"
        )
        html = """
        <table class="engineTable"><caption>Overall figures</caption>
        <tr><th>Player</th><th>Runs</th><th>Ave</th><th>SR</th></tr>
        <tr class="data1"><td><a href="/ci/content/player/348144.html">Babar Azam</a> (PAK)</td><td>4596</td><td>38.94</td><td>128.02</td></tr>
        </table><table><tr><td>Page <b>1</b> of <b>1</b></td><td>Showing <b>1</b> - <b>1</b> of <b>1</b></td></tr></table>
        """
        source = MemoryPageSource({expected_url: html})
        fetcher = Fetcher(Settings(min_interval=timedelta(seconds=0)), page_source=source)
        async with fetcher.call(budget=5) as call:
            proof = await build_proof_link(
                query,
                thresholds=(
                    Threshold(batting_metric("average"), Decimal("38.94")),
                    Threshold(batting_metric("strike_rate"), Decimal("128.02")),
                ),
                expected_player_ids=(348144,),
                call=call,
                as_of=date(2026, 10, 4),
            )

        assert proof.url == expected_url
        assert proof.confirmed is True
        assert proof.fetched is True
        assert source.requests == [expected_url]

    asyncio.run(run())


def test_proof_link_falls_back_with_formula_without_fetch_for_derived_metric() -> None:
    async def run() -> None:
        query = StatsguruQuery(**{"class": 2, "type": "batting", "size": 200})
        source = MemoryPageSource({})
        fetcher = Fetcher(Settings(min_interval=timedelta(seconds=0)), page_source=source)
        async with fetcher.call(budget=5) as call:
            proof = await build_proof_link(
                query,
                thresholds=(Threshold(batting_metric("innings_per_hundred"), Decimal("7")),),
                call=call,
                as_of=date(2026, 10, 4),
            )

        assert proof.label == "Input Statsguru table"
        assert proof.fetched is False
        assert proof.formula == "innings ÷ hundreds"
        assert source.requests == []

    asyncio.run(run())


def test_renderer_exact_markdown_with_live_freshness_and_profile_links() -> None:
    proof = "https://stats.cricinfo.com/ci/engine/stats/index.html?class=3;template=results;type=batting"
    rendered = render_answer(
        AnswerRenderInput(
            short_answer="Babar Azam is tied on displayed average and strike rate.",
            table=RenderedTable(
                headers=("Player", "Ave", "SR"),
                rows=(("Babar Azam", Decimal("38.94"), Decimal("128.02")),),
            ),
            method=("Used Statsguru displayed values; equal displayed values count as ties.",),
            assumptions=("Minimum: 1000 runs.",),
            proof_links=(
                __import__("crickey.proof", fromlist=["ProofLink"]).ProofLink(
                    "Confirmed Statsguru results", proof, True, True
                ),
            ),
            players=(RenderPlayer("Babar Azam", 348144),),
            as_of=date(2026, 10, 4),
            current_or_recent_matches=(
                RecentMatch(
                    "A v B, 1st T20I",
                    date(2026, 10, 4),
                    date(2026, 10, 4),
                    1,
                    "T20I # 1 - Live",
                    True,
                ),
            ),
        )
    )

    assert (
        rendered
        == """Babar Azam is tied on displayed average and strike rate.

| Player     | Ave   | SR     |
| ---------- | ----- | ------ |
| Babar Azam | 38.94 | 128.02 |

## Method
- Used Statsguru displayed values; equal displayed values count as ties.

## Assumptions
- Minimum: 1000 runs.

## Links
- [Confirmed Statsguru results](https://stats.cricinfo.com/ci/engine/stats/index.html?class=3;template=results;type=batting)
- [Babar Azam profile](https://stats.cricinfo.com/ci/content/player/348144.html)

As of: 4 Oct 2026
Freshness: newest match Statsguru included is A v B, 1st T20I (T20I # 1 - Live), ending 4 Oct 2026. Warning: a listed match may still be in progress."""
    )


def test_freshness_line_without_live_warning() -> None:
    line = freshness_line(
        (RecentMatch("A v B, 1st Test", date(2026, 9, 1), date(2026, 9, 4), 1, "Test # 1", False),),
        today=date(2026, 10, 4),
    )

    assert (
        line
        == "Freshness: newest match Statsguru included is A v B, 1st Test (Test # 1), ending 4 Sep 2026."
    )
