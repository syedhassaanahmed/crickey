from __future__ import annotations

# ruff: noqa: E501
import asyncio
from datetime import date, timedelta
from decimal import Decimal

from crickey.fetcher import Fetcher, Freshness, MemoryPageSource
from crickey.metrics import BATTING_METRICS, BetterDirection, batting_metric, rank_key
from crickey.parsers import RecentMatch
from crickey.parsers.results import parse_results_page
from crickey.proof import ProofLink, Threshold, build_proof_link
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
    row = {"Inns": 140, "100": 20, "50": 38, "BF": 7652, "NO": 16}

    assert batting_metric("innings_per_hundred").value_from_row(row) == Decimal("7")
    assert batting_metric("innings_per_fifty_plus").value_from_row(row) == Decimal(140) / Decimal(
        58
    )
    assert batting_metric("innings_per_fifty_plus").display_value(
        Decimal(140) / Decimal(58)
    ) == Decimal("2.41")
    assert batting_metric("balls_per_dismissal").value_from_row(row) == Decimal(7652) / Decimal(124)
    assert batting_metric("innings_per_hundred").value_from_row({"Inns": 10, "100": 0}) is None
    assert (
        batting_metric("balls_per_dismissal").value_from_row({"BF": 10, "Inns": 2, "NO": 2}) is None
    )


def test_metric_direction_default_minimums_and_displayed_ties() -> None:
    avg = batting_metric("average")
    iph = batting_metric("innings_per_hundred")
    sr = batting_metric("strike_rate")
    bpd = batting_metric("balls_per_dismissal")
    ip50 = batting_metric("innings_per_fifty_plus")

    assert avg.direction == BetterDirection.HIGHER
    assert iph.direction == BetterDirection.LOWER
    assert sr.better_than(Decimal("128.02"), Decimal("128.01")) is True
    assert bpd.better_than(Decimal("40.01"), Decimal("40.00")) is True
    assert ip50.better_than(Decimal("2.40"), Decimal("2.41")) is True
    assert avg.better_than(Decimal("50.01"), Decimal("50.00")) is True
    assert iph.better_than(Decimal("6.9"), Decimal("7.0")) is True
    assert avg.tied(Decimal("38.94"), Decimal("38.94")) is True
    assert avg.tied(Decimal("38.94"), Decimal("38.93")) is False
    assert avg.tied(None, None) is False
    assert avg.compare(None, Decimal("1")) == -1
    assert avg.compare(Decimal("1"), None) == 1
    assert rank_key(sr, Decimal("128.02")) < rank_key(sr, Decimal("128.01"))
    assert rank_key(ip50, Decimal("2.40")) < rank_key(ip50, Decimal("2.41"))
    assert rank_key(avg, None) > rank_key(avg, Decimal("1"))
    assert ip50.tied(Decimal("2.414"), Decimal("2.413")) is True
    assert ip50.display_value(Decimal("2.415")) == Decimal("2.42")


def test_default_minimum_table_and_unsupported_test_metrics_are_exact() -> None:
    table = {
        key: {
            class_id: (
                metric.default_minimum(class_id).field,
                metric.default_minimum(class_id).minimum,
            )
            if class_id in metric.supported_classes
            else None
            for class_id in (1, 2, 3, 6, 11)
        }
        for key, metric in BATTING_METRICS.items()
    }

    assert table == {
        "runs": {
            1: ("runs", 1000),
            2: ("runs", 500),
            3: ("runs", 250),
            6: ("runs", 500),
            11: ("runs", 1500),
        },
        "average": {
            1: ("innings", 20),
            2: ("innings", 20),
            3: ("innings", 20),
            6: ("innings", 30),
            11: ("innings", 30),
        },
        "strike_rate": {
            1: None,
            2: ("balls_faced", 500),
            3: ("balls_faced", 250),
            6: ("balls_faced", 500),
            11: ("balls_faced", 1000),
        },
        "hundreds": {
            1: ("hundreds", 5),
            2: ("hundreds", 5),
            3: ("hundreds", 1),
            6: ("hundreds", 1),
            11: ("hundreds", 10),
        },
        "fifties": {
            1: ("fifty_plus", 10),
            2: ("fifty_plus", 10),
            3: ("fifty_plus", 5),
            6: ("fifty_plus", 10),
            11: ("fifty_plus", 20),
        },
        "innings_per_hundred": {
            1: ("hundreds", 5),
            2: ("hundreds", 5),
            3: ("hundreds", 1),
            6: ("hundreds", 3),
            11: ("hundreds", 10),
        },
        "innings_per_fifty_plus": {
            1: ("hundreds", 5),
            2: ("hundreds", 5),
            3: ("hundreds", 1),
            6: ("hundreds", 3),
            11: ("hundreds", 10),
        },
        "balls_per_dismissal": {
            1: None,
            2: ("hundreds", 5),
            3: ("hundreds", 1),
            6: ("hundreds", 3),
            11: ("hundreds", 10),
        },
    }
    for key in ("strike_rate", "balls_per_dismissal"):
        metric = batting_metric(key)
        try:
            metric.value_from_row({"SR": None, "BF": None}, class_id=1)
        except ValueError as error:
            assert f"metric {key!r} is not supported for class 1" in str(error)
        else:
            raise AssertionError(f"{key} should be unsupported for Tests")


def test_every_default_minimum_compiles_as_statsguru_qualification() -> None:
    for metric in BATTING_METRICS.values():
        for class_id in metric.supported_classes:
            minimum = metric.default_minimum(class_id)
            url = StatsguruQuery(
                **{
                    "class": class_id,
                    "type": "batting",
                    "qualifications": (
                        Qualification(field=minimum.field, minimum=minimum.minimum),
                    ),
                }
            ).results_url(as_of=date(2026, 10, 4))

            assert f"qualval1={minimum.field}" in url
            assert "template=results" in url


def test_equal_displayed_values_from_synthetic_results_page_are_reported_as_ties() -> None:
    html = """
    <table class="engineTable"><caption>Overall figures</caption>
    <tr><th>Player</th><th>Runs</th><th>Ave</th></tr>
    <tr class="data1"><td><a href="/ci/content/player/1.html">Alpha</a> (AAA)</td><td>1000</td><td>38.94</td></tr>
    <tr class="data1"><td><a href="/ci/content/player/2.html">Beta</a> (BBB)</td><td>900</td><td>38.94</td></tr>
    </table><table><tr><td>Page <b>1</b> of <b>1</b></td><td>Showing <b>1</b> - <b>2</b> of <b>2</b></td></tr></table>
    """
    page = parse_results_page(html)
    metric = batting_metric("average")
    rows = sorted(
        page.table.to_dict("records"),
        key=lambda row: rank_key(metric, metric.value_from_row(row)),
    )

    assert [row["player_name"] for row in rows] == ["Alpha", "Beta"]
    assert metric.tied(metric.value_from_row(rows[0]), metric.value_from_row(rows[1])) is True


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
        country_name = await resolver.resolve_player("Babar", class_id=3, country="Pakistan")
        unknown = await resolver.resolve_player("No Such", class_id=3)

        assert unique.status == ResolveStatus.MATCH
        assert unique.match is not None and unique.match.player_id == 348144
        assert ambiguous.needs_clarification is True
        assert [candidate.name for candidate in ambiguous.candidates] == [
            "Babar Azam",
            "Babar Hayat",
        ]
        assert country.match is not None and country.match.player_id == 539305
        assert country_name.match is not None and country_name.match.player_id == 348144
        assert unknown.needs_clarification is True and unknown.candidates == ()

    asyncio.run(run())


def test_player_resolution_ranks_before_candidate_cap_and_refetches_cached_miss() -> None:
    async def run() -> None:
        url = "https://stats.cricinfo.com/ci/engine/stats/analysis.html?search=Babar;template=analysis"
        stale = "<html></html>"
        fresh = """
        <table>
        <tr><td>Babar A</td><td>AAA</td><td><a href="/ci/engine/player/1.html?class=6;type=allround">Twenty20 matches player</a> (2020 - 2021, 12 matches)</td></tr>
        <tr><td>Babar B</td><td>BBB</td><td><a href="/ci/engine/player/2.html?class=6;type=allround">Twenty20 matches player</a> (2020 - 2021, 20 matches)</td></tr>
        <tr><td>Babar C</td><td>CCC</td><td><a href="/ci/engine/player/3.html?class=6;type=allround">Twenty20 matches player</a> (2020 - 2021, 30 matches)</td></tr>
        <tr><td>Babar D</td><td>DDD</td><td><a href="/ci/engine/player/4.html?class=6;type=allround">Twenty20 matches player</a> (2020 - 2021, 40 matches)</td></tr>
        <tr><td>Babar E</td><td>EEE</td><td><a href="/ci/engine/player/5.html?class=6;type=allround">Twenty20 matches player</a> (2020 - 2021, 50 matches)</td></tr>
        <tr><td>Babar Azam</td><td>PAK</td><td><a href="/ci/engine/player/348144.html?class=6;type=allround">Twenty20 matches player</a> (2012/13 - 2026, 359 matches)</td></tr>
        <tr><td>Babar Hayat</td><td>HKG</td><td><a href="/ci/engine/player/539305.html?class=6;type=allround">Twenty20 matches player</a> (2012 - 2026, 180 matches)</td></tr>
        </table>
        """
        source = MemoryPageSource({url: stale})
        fetcher = Fetcher(Settings(min_interval=timedelta(seconds=0)), page_source=source)
        resolver = NameResolver(fetcher)

        first = await resolver.resolve_player("Babar", class_id=6)
        source.pages[url] = fresh
        second = await resolver.resolve_player("Babar", class_id=6)

        assert first.needs_clarification is True and first.candidates == ()
        assert source.requests == [url, url]
        assert [candidate.player_id for candidate in second.candidates] == [348144, 539305, 5, 4, 3]

    asyncio.run(run())


def test_player_resolution_uses_passed_call_and_lookup_freshness_and_full_name_match() -> None:
    class RecordingCall:
        def __init__(self, html: str) -> None:
            self.html = html
            self.calls: list[tuple[str, Freshness, bool]] = []

        async def fetch(self, url: str, *, freshness, force_refetch: bool = False) -> str:
            self.calls.append((url, freshness, force_refetch))
            return self.html

    async def run() -> None:
        html = """
        <table>
        <tr><td>Other Babar</td><td>PAK</td><td><a href="/ci/engine/player/1.html?class=3;type=allround">Twenty20 Internationals player</a> (2016 - 2026, 1 match)</td></tr>
        <tr><td>Babar Azam (Mohammad Babar Azam)</td><td>PAK</td><td><a href="/ci/engine/player/348144.html?class=3;type=allround">Twenty20 Internationals player</a> (2016 - 2026, 145 matches)</td></tr>
        </table>
        """
        call = RecordingCall(html)
        resolver = NameResolver(
            Fetcher(Settings(min_interval=timedelta(seconds=0)), page_source=MemoryPageSource({}))
        )
        result = await resolver.resolve_player("Mohammad Babar Azam", class_id=3, call=call)

        assert result.match is not None and result.match.player_id == 348144
        assert call.calls == [
            (
                "https://stats.cricinfo.com/ci/engine/stats/analysis.html?search=Mohammad+Babar+Azam;template=analysis",
                Freshness.LOOKUP,
                False,
            )
        ]

    asyncio.run(run())


def test_player_resolution_does_not_auto_match_unrelated_single_candidate_after_filtering() -> None:
    async def run() -> None:
        s_kohli_url = "https://stats.cricinfo.com/ci/engine/stats/analysis.html?search=S+Kohli;template=analysis"
        s_kohli = """
        <table>
        <tr><td>S Kohli</td><td>IND</td><td><a href="/ci/engine/player/1.html?class=9;type=allround">Women's One-Day Internationals player</a> (2020 - 2021, 1 match)</td></tr>
        <tr><td>PS Kohli (Parth Kohli)</td><td>IND</td><td><a href="/ci/engine/player/2.html?class=6;type=allround">Twenty20 matches player</a> (2020 - 2021, 10 matches)</td></tr>
        </table>
        """
        kohli_url = "https://stats.cricinfo.com/ci/engine/stats/analysis.html?search=kohli;template=analysis"
        kohli = """
        <table>
        <tr><td>V Kohli</td><td>IND</td><td><a href="/ci/engine/player/253802.html?class=1;type=allround">Test matches player</a> (2011 - 2026, 120 matches)</td></tr>
        <tr><td>Pawan Kohli</td><td>IND</td><td><a href="/ci/engine/player/3.html?class=1;type=allround">Test matches player</a> (2020 - 2021, 1 match)</td></tr>
        <tr><td>Taruwar Kohli</td><td>IND</td><td><a href="/ci/engine/player/4.html?class=1;type=allround">Test matches player</a> (2020 - 2021, 1 match)</td></tr>
        </table>
        """
        resolver = NameResolver(
            Fetcher(
                Settings(min_interval=timedelta(seconds=0)),
                page_source=MemoryPageSource({s_kohli_url: s_kohli, kohli_url: kohli}),
            )
        )

        s_result = await resolver.resolve_player("S Kohli", class_id=6)
        kohli_result = await resolver.resolve_player("kohli", class_id=1)

        assert s_result.needs_clarification is True
        assert s_result.match is None
        assert [candidate.name for candidate in s_result.candidates] == ["PS Kohli"]
        assert kohli_result.needs_clarification is True
        assert kohli_result.match is None
        assert [candidate.name for candidate in kohli_result.candidates] == [
            "V Kohli",
            "Pawan Kohli",
            "Taruwar Kohli",
        ]

    asyncio.run(run())


def test_country_name_filter_maps_scotland_and_unknown_country_falls_back() -> None:
    async def run() -> None:
        url = "https://stats.cricinfo.com/ci/engine/stats/analysis.html?search=Cross;template=analysis"
        html = """
        <table>
        <tr><td>Matthew Cross</td><td>SCOT</td><td><a href="/ci/engine/player/1.html?class=3;type=allround">Twenty20 Internationals player</a> (2019 - 2026, 70 matches)</td></tr>
        <tr><td>Ben Cross</td><td>ENG</td><td><a href="/ci/engine/player/2.html?class=3;type=allround">Twenty20 Internationals player</a> (2019 - 2026, 10 matches)</td></tr>
        </table>
        """
        resolver = NameResolver(
            Fetcher(
                Settings(min_interval=timedelta(seconds=0)),
                page_source=MemoryPageSource({url: html}),
            )
        )

        scotland = await resolver.resolve_player("Cross", class_id=3, country="Scotland")
        unknown = await resolver.resolve_player("Cross", class_id=3, country="Atlantis")

        assert scotland.match is not None and scotland.match.name == "Matthew Cross"
        assert [candidate.name for candidate in unknown.candidates] == [
            "Matthew Cross",
            "Ben Cross",
        ]
        assert unknown.note == "country 'Atlantis' could not be applied"

    asyncio.run(run())


def test_country_filter_uses_explicit_real_codes_and_never_auto_matches_on_miss() -> None:
    async def run() -> None:
        smith_url = "https://stats.cricinfo.com/ci/engine/stats/analysis.html?search=Smith;template=analysis"
        smith = """
        <table>
        <tr><td>John Smith</td><td>AUS</td><td><a href="/ci/engine/player/1.html?class=3;type=allround">Twenty20 Internationals player</a> (2019 - 2026, 70 matches)</td></tr>
        <tr><td>John Smith</td><td>AUT</td><td><a href="/ci/engine/player/2.html?class=3;type=allround">Twenty20 Internationals player</a> (2019 - 2026, 10 matches)</td></tr>
        </table>
        """
        faisal_url = "https://stats.cricinfo.com/ci/engine/stats/analysis.html?search=Faisal+Khan;template=analysis"
        faisal = """
        <table>
        <tr><td>Faisal Khan</td><td>SA</td><td><a href="/ci/engine/player/3.html?class=3;type=allround">Twenty20 Internationals player</a> (2019 - 2026, 70 matches)</td></tr>
        <tr><td>Faisal Khan</td><td>KSA</td><td><a href="/ci/engine/player/4.html?class=3;type=allround">Twenty20 Internationals player</a> (2019 - 2026, 10 matches)</td></tr>
        </table>
        """
        kohli_url = "https://stats.cricinfo.com/ci/engine/stats/analysis.html?search=Kohli;template=analysis"
        kohli = """
        <table>
        <tr><td>V Kohli</td><td>IND</td><td><a href="/ci/engine/player/253802.html?class=1;type=allround">Test matches player</a> (2011 - 2026, 120 matches)</td></tr>
        </table>
        """
        resolver = NameResolver(
            Fetcher(
                Settings(min_interval=timedelta(seconds=0)),
                page_source=MemoryPageSource(
                    {smith_url: smith, faisal_url: faisal, kohli_url: kohli}
                ),
            )
        )

        austria = await resolver.resolve_player("Smith", class_id=3, country="Austria")
        saudi = await resolver.resolve_player("Faisal Khan", class_id=3, country="Saudi Arabia")
        germany = await resolver.resolve_player("Kohli", class_id=1, country="Germany")

        assert austria.match is not None and austria.match.player_id == 2
        assert saudi.match is not None and saudi.match.player_id == 4
        assert germany.match is None
        assert [candidate.name for candidate in germany.candidates] == ["V Kohli"]
        assert germany.note == "country 'Germany' did not match any candidates"

    asyncio.run(run())


def test_team_ground_and_trophy_resolution() -> None:
    async def run() -> None:
        class RecordingCall:
            def __init__(self, html: str) -> None:
                self.html = html
                self.calls: list[tuple[str, Freshness, bool]] = []

            async def fetch(self, url: str, *, freshness, force_refetch: bool = False) -> str:
                self.calls.append((url, freshness, force_refetch))
                return self.html

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
        call = RecordingCall(form)

        assert resolver.resolve_team("India", class_id=1).match.value == 6
        assert resolver.resolve_trophy("Ashes", class_id=1).match.value == 1
        ground = await resolver.resolve_ground("Lord's", class_id=1, call=call)
        assert ground.match is not None and ground.match.value == 132
        assert call.calls == [(form_url, Freshness.LOOKUP, False)]

    asyncio.run(run())


def test_career_span_and_first_last_year_periods_from_player_form() -> None:
    async def run() -> None:
        form_url = "https://stats.cricinfo.com/ci/engine/player/348144.html?class=3;type=batting"
        form = """
        <form name="gurumenu"><input type="hidden" name="spanmin0" value="07 Sep 2016"><input type="hidden" name="spanmax0" value="29 Feb 2024"></form>
        """
        source = MemoryPageSource({form_url: form})
        fetcher = Fetcher(Settings(min_interval=timedelta(seconds=0)), page_source=source)
        resolver = PeriodResolver(fetcher)

        career = await resolver.career_span(348144, class_id=3)
        async with fetcher.call(budget=5) as call:
            first = await resolver.resolve_symbolic(
                348144,
                class_id=3,
                period=SymbolicPeriod(kind=SymbolicPeriodKind.FIRST_YEARS, years=2),
                call=call,
            )
            last = await resolver.resolve_symbolic(
                348144,
                class_id=3,
                period=SymbolicPeriod(kind=SymbolicPeriodKind.LAST_YEARS, years=1),
                call=call,
            )

        assert career == ResolvedPeriod(start=date(2016, 9, 7), end=date(2024, 2, 29))
        assert first == ResolvedPeriod(start=date(2016, 9, 7), end=date(2018, 9, 7))
        assert last == ResolvedPeriod(start=date(2023, 2, 28), end=date(2024, 2, 29))

    asyncio.run(run())


def test_career_span_falls_back_to_innings_list_with_passed_call_and_freshness() -> None:
    class RecordingCall:
        def __init__(self, pages: dict[str, str]) -> None:
            self.pages = pages
            self.calls: list[tuple[str, Freshness]] = []

        async def fetch(self, url: str, *, freshness, force_refetch: bool = False) -> str:
            assert force_refetch is False
            self.calls.append((url, freshness))
            return self.pages[url]

    async def run() -> None:
        from crickey.query import PlayerPageSpec

        form_url = "https://stats.cricinfo.com/ci/engine/player/348144.html?class=3;type=batting"
        innings_url = PlayerPageSpec(
            player_id=348144,
            **{"class": 3, "type": "batting", "view": "innings"},
        ).url()
        innings_html = """
        <table class="engineTable"><caption>Career averages</caption>
        <tr><th></th><th>Span</th><th>Mat</th></tr>
        <tr class="data1"><td></td><td>2016-2026</td><td>145</td></tr>
        </table>
        <table class="engineTable"><caption>Innings by innings list</caption>
        <tr><th>Runs</th><th>Start Date</th><th></th></tr>
        <tr class="data1"><td>1</td><td>7 Sep 2016</td><td><a href="/ci/engine/match/1.html">T20I # 1</a></td></tr>
        <tr class="data1"><td>2</td><td>24 Feb 2026</td><td><a href="/ci/engine/match/2.html">T20I # 2</a></td></tr>
        </table>
        """
        call = RecordingCall({form_url: "<html></html>", innings_url: innings_html})
        resolver = PeriodResolver(
            Fetcher(Settings(min_interval=timedelta(seconds=0)), page_source=MemoryPageSource({}))
        )

        career = await resolver.career_span(348144, class_id=3, call=call)

        assert career == ResolvedPeriod(start=date(2016, 9, 7), end=date(2026, 2, 24))
        assert call.calls == [(form_url, Freshness.LOOKUP), (innings_url, Freshness.RECENT)]

    asyncio.run(run())


def test_resolve_symbolic_uses_passed_call() -> None:
    class RecordingCall:
        def __init__(self, html: str) -> None:
            self.html = html
            self.calls: list[tuple[str, Freshness]] = []

        async def fetch(self, url: str, *, freshness, force_refetch: bool = False) -> str:
            assert force_refetch is False
            self.calls.append((url, freshness))
            return self.html

    async def run() -> None:
        form_url = "https://stats.cricinfo.com/ci/engine/player/348144.html?class=3;type=batting"
        form = """
        <form name="gurumenu"><input type="hidden" name="spanmin0" value="07 Sep 2016"><input type="hidden" name="spanmax0" value="29 Feb 2024"></form>
        """
        resolver = PeriodResolver(
            Fetcher(Settings(min_interval=timedelta(seconds=0)), page_source=MemoryPageSource({}))
        )
        call = RecordingCall(form)

        period = await resolver.resolve_symbolic(
            348144,
            class_id=3,
            period=SymbolicPeriod(kind=SymbolicPeriodKind.LAST_YEARS, years=1),
            call=call,
        )

        assert period == ResolvedPeriod(start=date(2023, 2, 28), end=date(2024, 2, 29))
        assert call.calls == [(form_url, Freshness.LOOKUP)]

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
        <tr class="data1"><td><a href="/ci/content/player/539305.html">Babar Hayat</a> (HKG)</td><td>2000</td><td>40.00</td><td>130.00</td></tr>
        <tr class="data1"><td><a href="/ci/content/player/348144.html">Babar Azam</a> (PAK)</td><td>4596</td><td>38.94</td><td>128.02</td></tr>
        </table><table><tr><td>Page <b>1</b> of <b>1</b></td><td>Showing <b>1</b> - <b>2</b> of <b>2</b></td></tr></table>
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
                expected_player_ids=(348144, 539305),
                call=call,
                as_of=date(2026, 10, 4),
            )

        assert proof.url == expected_url
        assert proof.confirmed is True
        assert proof.fetched is True
        assert proof.row_count == 2
        assert proof.label.startswith("Confirmed Statsguru results: T20I batting")
        assert "at least 38.94 average" in proof.label
        assert "at least 128.02 strike rate" in proof.label
        assert source.requests == [expected_url]

    asyncio.run(run())


def test_proof_confirmation_mismatch_no_expected_ids_and_no_records_fall_back() -> None:
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
        proof_url = (
            "https://stats.cricinfo.com/ci/engine/stats/index.html?"
            "class=3;orderby=batting_average;qualmin1=1000;qualmin2=38.94;"
            "qualval1=runs;qualval2=batting_average;size=200;"
            "spanmax1=24+Feb+2026;spanmin1=07+Sep+2016;spanval1=span;template=results;type=batting"
        )
        input_url = (
            "https://stats.cricinfo.com/ci/engine/stats/index.html?"
            "class=3;orderby=batting_average;qualmin1=1000;qualval1=runs;size=200;"
            "spanmax1=24+Feb+2026;spanmin1=07+Sep+2016;spanval1=span;template=results;type=batting"
        )
        mismatch_html = """
        <table class="engineTable"><caption>Overall figures</caption>
        <tr><th>Player</th><th>Runs</th><th>Ave</th></tr>
        <tr class="data1"><td><a href="/ci/content/player/1.html">Wrong Player</a> (AAA)</td><td>1000</td><td>38.94</td></tr>
        </table><table><tr><td>Page <b>1</b> of <b>1</b></td><td>Showing <b>1</b> - <b>1</b> of <b>1</b></td></tr></table>
        """
        no_records_html = """
        <table class="engineTable"><caption>Overall figures</caption>
        <tr class="data1"><td>No records available to match this query</td></tr></table>
        """
        source = MemoryPageSource({proof_url: mismatch_html})
        fetcher = Fetcher(Settings(min_interval=timedelta(seconds=0)), page_source=source)
        async with fetcher.call(budget=5) as call:
            mismatch = await build_proof_link(
                query,
                thresholds=(Threshold(batting_metric("average"), Decimal("38.94")),),
                expected_player_ids=(348144,),
                call=call,
                as_of=date(2026, 10, 4),
            )
        no_records_source = MemoryPageSource({proof_url: no_records_html})
        no_records_fetcher = Fetcher(
            Settings(min_interval=timedelta(seconds=0)), page_source=no_records_source
        )
        async with no_records_fetcher.call(budget=5) as call:
            no_records = await build_proof_link(
                query,
                thresholds=(Threshold(batting_metric("average"), Decimal("38.94")),),
                expected_player_ids=(348144,),
                call=call,
                as_of=date(2026, 10, 4),
            )
        no_expected = await build_proof_link(
            query,
            thresholds=(Threshold(batting_metric("average"), Decimal("38.94")),),
            as_of=date(2026, 10, 4),
        )

        assert mismatch.confirmed is False
        assert "confirmation did not match Statsguru rows" in mismatch.label
        assert mismatch.url == input_url
        assert no_records.confirmed is False
        assert no_records.url == input_url
        assert no_expected.confirmed is False
        assert "confirmation needs expected player IDs" in no_expected.label

    asyncio.run(run())


def test_proof_confirmation_requires_matching_total_row_count() -> None:
    async def run() -> None:
        query = StatsguruQuery(
            **{
                "class": 3,
                "type": "batting",
                "period": ResolvedPeriod(start=date(2016, 9, 7), end=date(2026, 2, 24)),
                "qualifications": (Qualification(field="runs", minimum=1000),),
                "size": 200,
            }
        )
        proof_url = (
            "https://stats.cricinfo.com/ci/engine/stats/index.html?"
            "class=3;qualmin1=1000;qualmin2=38.94;qualval1=runs;"
            "qualval2=batting_average;size=200;spanmax1=24+Feb+2026;"
            "spanmin1=07+Sep+2016;spanval1=span;template=results;type=batting"
        )
        input_url = (
            "https://stats.cricinfo.com/ci/engine/stats/index.html?"
            "class=3;qualmin1=1000;qualval1=runs;size=200;spanmax1=24+Feb+2026;"
            "spanmin1=07+Sep+2016;spanval1=span;template=results;type=batting"
        )
        html = """
        <table class="engineTable"><caption>Overall figures</caption>
        <tr><th>Player</th><th>Ave</th></tr>
        <tr class="data1"><td><a href="/ci/content/player/348144.html">Babar Azam</a> (PAK)</td><td>38.94</td></tr>
        </table><table><tr><td>Page <b>1</b> of <b>2</b></td><td>Showing <b>1</b> - <b>1</b> of <b>2</b></td></tr></table>
        """
        source = MemoryPageSource({proof_url: html})
        fetcher = Fetcher(Settings(min_interval=timedelta(seconds=0)), page_source=source)
        async with fetcher.call(budget=5) as call:
            proof = await build_proof_link(
                query,
                thresholds=(Threshold(batting_metric("average"), Decimal("38.94")),),
                expected_player_ids=(348144,),
                call=call,
                as_of=date(2026, 10, 4),
            )

        assert proof.confirmed is False
        assert proof.row_count == 2
        assert proof.url == input_url

    asyncio.run(run())


def test_proof_falls_back_when_not_expressible_or_too_many_qualifications_without_fetch() -> None:
    async def run() -> None:
        query = StatsguruQuery(
            **{
                "class": 2,
                "type": "batting",
                "qualifications": (Qualification(field="runs", minimum=1000),),
                "size": 200,
            }
        )
        source = MemoryPageSource({})
        fetcher = Fetcher(Settings(min_interval=timedelta(seconds=0)), page_source=source)
        async with fetcher.call(budget=5) as call:
            not_expressible = await build_proof_link(
                query,
                thresholds=(Threshold(batting_metric("innings_per_hundred"), Decimal("7")),),
                call=call,
                as_of=date(2026, 10, 4),
            )
            any_mutant_guard = await build_proof_link(
                query,
                thresholds=(
                    Threshold(batting_metric("average"), Decimal("40")),
                    Threshold(batting_metric("innings_per_hundred"), Decimal("7")),
                ),
                expected_player_ids=(1,),
                call=call,
                as_of=date(2026, 10, 4),
            )
            too_many = await build_proof_link(
                query.model_copy(
                    update={
                        "qualifications": (
                            Qualification(field="runs", minimum=1000),
                            Qualification(field="innings", minimum=20),
                        )
                    }
                ),
                thresholds=(
                    Threshold(batting_metric("average"), Decimal("40")),
                    Threshold(batting_metric("strike_rate"), Decimal("90")),
                ),
                expected_player_ids=(1,),
                call=call,
                as_of=date(2026, 10, 4),
            )

        assert not_expressible.label.startswith("Input Statsguru table: ODI batting")
        assert not_expressible.fetched is False
        assert not_expressible.formula == "innings ÷ hundreds"
        assert "qualmin2=40" not in any_mutant_guard.url
        assert too_many.fetched is False
        assert "qualmin3" not in too_many.url
        assert source.requests == []

    asyncio.run(run())


def test_proof_requires_call_and_uses_query_freshness() -> None:
    class RecordingCall:
        def __init__(self, html: str) -> None:
            self.html = html
            self.freshness = None

        async def fetch(self, url: str, *, freshness, force_refetch: bool = False) -> str:
            self.freshness = freshness
            assert force_refetch is False
            return self.html

    async def run() -> None:
        query = StatsguruQuery(
            **{
                "class": 3,
                "type": "batting",
                "period": ResolvedPeriod(start=date(2018, 1, 1), end=date(2018, 12, 31)),
            }
        )
        html = """
        <table class="engineTable"><caption>Overall figures</caption>
        <tr><th>Player</th><th>Ave</th></tr>
        <tr class="data1"><td><a href="/ci/content/player/348144.html">Babar Azam</a> (PAK)</td><td>38.94</td></tr>
        </table><table><tr><td>Page <b>1</b> of <b>1</b></td><td>Showing <b>1</b> - <b>1</b> of <b>1</b></td></tr></table>
        """
        call = RecordingCall(html)
        proof = await build_proof_link(
            query,
            thresholds=(Threshold(batting_metric("average"), Decimal("38.94")),),
            expected_player_ids=(348144,),
            call=call,
            as_of=date(2026, 10, 4),
        )

        assert proof.confirmed is True
        assert call.freshness is Freshness.SETTLED
        open_query = StatsguruQuery(**{"class": 3, "type": "batting"})
        open_call = RecordingCall(html)
        open_proof = await build_proof_link(
            open_query,
            thresholds=(Threshold(batting_metric("average"), Decimal("38.94")),),
            expected_player_ids=(348144,),
            call=open_call,
            as_of=date(2026, 10, 4),
        )
        assert open_proof.confirmed is True
        assert open_call.freshness is Freshness.RECENT
        try:
            await build_proof_link(
                query,
                thresholds=(Threshold(batting_metric("average"), Decimal("38.94")),),
                expected_player_ids=(348144,),
                as_of=date(2026, 10, 4),
            )
        except ValueError as error:
            assert "fetch call is required" in str(error)
        else:
            raise AssertionError("expressible proof confirmation should require call=")

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
            proof_links=(ProofLink("Confirmed Statsguru results", proof, True, True),),
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
- Answer proof: [Confirmed Statsguru results](https://stats.cricinfo.com/ci/engine/stats/index.html?class=3;template=results;type=batting)
- [Babar Azam profile](https://stats.cricinfo.com/ci/content/player/348144.html)

As of: 4 Oct 2026
Freshness: newest match Statsguru included is A v B, 1st T20I (T20I # 1 - Live), ending 4 Oct 2026. Warning: a listed match may still be in progress."""
    )


def test_renderer_exact_markdown_for_fallback_formula_link() -> None:
    proof = "https://stats.cricinfo.com/ci/engine/stats/index.html?class=2;template=results;type=batting"
    rendered = render_answer(
        AnswerRenderInput(
            short_answer="Babar Azam took 2.41 innings per fifty-plus score.",
            table=RenderedTable(
                headers=("Player", "Inns/50+"),
                rows=(("Babar Azam", Decimal("2.41")),),
            ),
            method=("Calculated derived metric from totals.",),
            assumptions=(),
            proof_links=(
                ProofLink(
                    "Input Statsguru table: ODI batting",
                    proof,
                    False,
                    False,
                    formula="innings ÷ (hundreds + fifties)",
                ),
            ),
            players=(RenderPlayer("Babar Azam", 348144),),
            as_of=date(2026, 10, 4),
        )
    )

    assert (
        rendered
        == """Babar Azam took 2.41 innings per fifty-plus score.

| Player     | Inns/50+ |
| ---------- | -------- |
| Babar Azam | 2.41     |

## Method
- Calculated derived metric from totals.

## Assumptions
- None.

## Links
- Input/method link: [Input Statsguru table: ODI batting](https://stats.cricinfo.com/ci/engine/stats/index.html?class=2;template=results;type=batting). Formula: innings ÷ (hundreds + fifties).
- [Babar Azam profile](https://stats.cricinfo.com/ci/content/player/348144.html)

As of: 4 Oct 2026
Freshness: Statsguru did not list any current or recent matches on the proof page."""
    )


def test_freshness_line_without_live_warning() -> None:
    line = freshness_line(
        (
            RecentMatch(
                "Old v Older, 1st Test",
                date(2026, 8, 1),
                date(2026, 8, 4),
                1,
                "Test # 0",
                False,
            ),
            RecentMatch(
                "A v B, 1st Test",
                date(2026, 9, 1),
                date(2026, 9, 4),
                2,
                "Test # 1",
                False,
            ),
        ),
        today=date(2026, 10, 4),
    )
    warning = freshness_line(
        (
            RecentMatch(
                "Live v Match, 1st Test",
                date(2026, 9, 1),
                date(2026, 9, 3),
                2,
                "Test # 1 - Live",
                True,
            ),
            RecentMatch(
                "A v B, 2nd Test",
                date(2026, 10, 1),
                date(2026, 10, 3),
                3,
                "Test # 2",
                False,
            ),
        ),
        today=date(2026, 10, 4),
    )
    empty = freshness_line((), today=date(2026, 10, 4))

    assert (
        line
        == "Freshness: newest match Statsguru included is A v B, 1st Test (Test # 1), ending 4 Sep 2026."
    )
    assert warning.endswith("Warning: a listed match may still be in progress.")
    assert (
        empty
        == "Freshness: Statsguru did not list any current or recent matches on the proof page."
    )
