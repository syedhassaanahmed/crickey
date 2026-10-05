from __future__ import annotations

# ruff: noqa: E501
import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest
from mcp import Client

from crickey.fetcher import Fetcher, FetchResponse, Freshness, MemoryPageSource
from crickey.query import Qualification, StatsguruQuery
from crickey.server import _freshness_for_query, create_server
from crickey.settings import Settings

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeClock:
    def __init__(self) -> None:
        self.monotonic_time = 0.0
        self.wall_time = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.monotonic_time

    def now(self) -> datetime:
        return self.wall_time + timedelta(seconds=self.monotonic_time)

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.monotonic_time += seconds
        await asyncio.sleep(0)


class RecordingFetcher(Fetcher):
    def __init__(
        self, settings_: Settings, *, clock: FakeClock, page_source: MemoryPageSource
    ) -> None:
        super().__init__(settings_, clock=clock, page_source=page_source)
        self.budgets: list[float] = []

    def call(self, *, budget, progress=None):
        seconds = budget.total_seconds() if hasattr(budget, "total_seconds") else float(budget)
        self.budgets.append(seconds)
        return super().call(budget=budget, progress=progress)


def settings(**overrides: object) -> Settings:
    values = {
        "min_interval": timedelta(seconds=0),
        "max_retries": 0,
        "block_pauses": (timedelta(seconds=10),),
        "max_pages": 4,
        "cache_max_mb": 1,
        "recent_ttl": timedelta(seconds=30),
        "port": 8765,
        "in_container": False,
    }
    values.update(overrides)
    return Settings(**values)


def player_search_url(search: str) -> str:
    return f"https://stats.cricinfo.com/ci/engine/stats/analysis.html?search={search};template=analysis"


def results_url(query: StatsguruQuery, *, page: int | None = None) -> str:
    if page is not None:
        query = query.model_copy(update={"page": page})
    return query.results_url(as_of=FakeClock().now().date())


def result_page(rows: list[str], *, page: int = 1, pages: int = 1, total: int | None = None) -> str:
    total = len(rows) if total is None else total
    start = 1 if not rows else (page - 1) * 200 + 1
    end = start + len(rows) - 1 if rows else 0
    return f"""
    <html><body>
    <table class="engineTable"><caption>Overall figures</caption>
    <tr><th>Player</th><th>Span</th><th>Mat</th><th>Runs</th><th>HS</th><th>Ave</th><th>100</th></tr>
    {"".join(rows)}
    </table>
    <table><tr><td>Page <b>{page}</b> of <b>{pages}</b></td><td>Showing <b>{start}</b> - <b>{end}</b> of <b>{total}</b></td></tr></table>
    <table class="engineTable"><tr class="data2"><td><b>Statsguru includes the following current or recent ODIs:</b></td></tr>
    <tr class="data2"><td>Example XI v Sample XI at Testville, 1st ODI, Oct 3, 2026 [<a href="/ci/engine/match/1.html">ODI # 1</a>]</td></tr>
    </table>
    </body></html>
    """


def no_records_page() -> str:
    return """
    <html><body>
    <table class="engineTable"><caption>Overall figures</caption>
    <tr class="data1"><td>No records available to match this query</td></tr>
    </table>
    </body></html>
    """


def bowling_page() -> str:
    return """
    <html><body>
    <table class="engineTable"><caption>Overall figures</caption>
    <tr><th>Player</th><th>Span</th><th>Mat</th><th>Overs</th><th>Wkts</th></tr>
    <tr class="data1"><td><a href="/ci/content/player/793463.html">Rashid Khan</a> (AFG)</td><td>2015-2026</td><td>118</td><td>449.5</td><td>197</td></tr>
    </table>
    <table><tr><td>Page <b>1</b> of <b>1</b></td><td>Showing <b>1</b> - <b>1</b> of <b>1</b></td></tr></table>
    </body></html>
    """


def team_page() -> str:
    return """
    <html><body>
    <table class="engineTable"><caption>Overall figures</caption>
    <tr><th>Team</th><th>Span</th><th>Mat</th><th>Won</th><th>Lost</th><th>Tied</th><th>NR</th><th>W/L</th><th>Ave</th><th>RPO</th><th>Inns</th><th>HS</th><th>LS</th></tr>
    <tr class="data1"><td><a href="/ci/content/team/99.html">Example XI</a></td><td>2020-2026</td><td>14</td><td>9</td><td>4</td><td>0</td><td>1</td><td>2.250</td><td>32.10</td><td>7.65</td><td>14</td><td>210</td><td>80</td></tr>
    </table>
    <table><tr><td>Page <b>1</b> of <b>1</b></td><td>Showing <b>1</b> - <b>1</b> of <b>1</b></td></tr></table>
    </body></html>
    """


def row(player_id: int, name: str, team: str, runs: int, ave: str = "50.00") -> str:
    return f"""
    <tr class="data1"><td><a href="/ci/content/player/{player_id}.html">{name}</a> ({team})</td><td>2015-2026</td><td>10</td><td>{runs}</td><td>101*</td><td>{ave}</td><td>1</td></tr>
    """


def detailed_batting_page() -> str:
    return """
    <html><body>
    <table class="engineTable"><caption>Overall figures</caption>
    <tr><th>Player</th><th>Span</th><th>Mat</th><th>Inns</th><th>NO</th><th>Runs</th><th>HS</th><th>Ave</th><th>BF</th><th>SR</th><th>100</th><th>50</th><th>0</th></tr>
    <tr class="data1"><td><a href="/ci/content/player/348144.html">Babar Azam</a> (PAK)</td><td>2015-2026</td><td>143</td><td>140</td><td>16</td><td>6626</td><td>158</td><td>53.43</td><td>7652</td><td>86.59</td><td>20</td><td>38</td><td>5</td></tr>
    </table>
    <table><tr><td>Page <b>1</b> of <b>1</b></td><td>Showing <b>1</b> - <b>1</b> of <b>1</b></td></tr></table>
    </body></html>
    """


def sorted_column_page(headers: Sequence[str], row: Sequence[str]) -> str:
    header_html = "".join(f"<th>{header}</th>" for header in headers)
    row_html = "".join(f"<td>{value}</td>" for value in row)
    return f"""
    <html><body>
    <table class="engineTable"><caption>Overall figures</caption>
    <tr>{header_html}</tr>
    <tr class="data1">{row_html}</tr>
    </table>
    <table><tr><td>Page <b>1</b> of <b>1</b></td><td>Showing <b>1</b> - <b>1</b> of <b>1</b></td></tr></table>
    </body></html>
    """


def source_for(pages: dict[str, str]) -> MemoryPageSource:
    return MemoryPageSource(pages)


async def call_with_source(pages: dict[str, str], settings_: Settings | None = None):
    source = source_for(pages)
    clock = FakeClock()
    fetcher = Fetcher(settings_ or settings(), clock=clock, page_source=source)
    mcp = create_server(settings_ or settings(), fetcher=fetcher)
    client = Client(mcp)
    return source, clock, client


async def test_tool_listing_has_read_only_annotations_and_instructions() -> None:
    _, _, client = await call_with_source({})

    async with client:
        tools = (await client.list_tools()).tools
        by_name = {tool.name: tool for tool in tools}

        assert set(by_name) == {
            "better_than_player",
            "find_player",
            "leaderboard",
            "player_record",
            "query_stats",
        }
        assert by_name["find_player"].annotations.read_only_hint is True
        assert by_name["find_player"].annotations.open_world_hint is True
        assert by_name["query_stats"].annotations.read_only_hint is True
        assert by_name["leaderboard"].annotations.read_only_hint is True
        assert by_name["better_than_player"].annotations.read_only_hint is True
        assert by_name["player_record"].annotations.read_only_hint is True
        assert by_name["find_player"].description.startswith("Example:")
        assert by_name["query_stats"].description.startswith("Example:")
        assert by_name["leaderboard"].description.startswith("Example:")
        assert by_name["better_than_player"].description.startswith("Example:")
        assert by_name["player_record"].description.startswith("Example:")
        assert (
            "Average number of innings taken per ODI century" in by_name["leaderboard"].description
        )
        assert (
            "Which players have scored Test hundreds more frequently"
            in by_name["better_than_player"].description
        )
        assert "better average and strike rate" in by_name["better_than_player"].description
        assert "Test batting average in the last Y years" in by_name["player_record"].description
        assert "ODI World Cups" in by_name["player_record"].description
        assert "Which Babar played ODIs?" in by_name["find_player"].description
        assert (
            "Who has the most ODI wickets against Australia?" in by_name["query_stats"].description
        )
        assert (
            "Lists work for every filter except batting_fielding_first, captain, keeper, outs "
            "and toss." in by_name["query_stats"].description
        )
        assert client.instructions == "\n".join(
            [
                "Prefer crickey's answer tools and show answer_markdown as-is when they return it.",
                "Do not do multi-row arithmetic yourself; use crickey tools for comparisons and derived rates.",
                "Cite only links that came from crickey tools.",
                'Read "T20" as T20I unless a domestic or franchise league is named.',
            ]
        )
        query_schema = str(by_name["query_stats"].input_schema)
        assert "'class'" in query_schema
        assert "'type'" in query_schema
        assert "'orderby'" in query_schema
        assert "'qualval1'" in query_schema


async def test_query_stats_fetch_false_allows_repeated_team_link() -> None:
    _, _, client = await call_with_source({})

    async with client:
        result = await client.call_tool(
            "query_stats",
            {
                "fetch": False,
                "query": {
                    "class": 1,
                    "type": "bowling",
                    "continent": 2,
                    "orderby": "wickets",
                    "team": [1, 3],
                },
            },
        )

    assert result.structured_content["link"] == (
        "https://stats.cricinfo.com/ci/engine/stats/index.html?"
        "class=1;continent=2;orderby=wickets;spanmax1=04+Oct+2026;"
        "spanmin1=15+Mar+1877;spanval1=span;team=1;team=3;template=results;type=bowling"
    )


async def test_find_player_unique_ambiguous_and_unknown() -> None:
    html = """
    <table>
    <tr><td>Babar Azam</td><td>PAK</td><td><a href="/ci/engine/player/348144.html?class=3;type=allround">Twenty20 Internationals player</a> (2016 - 2026, 145 matches)</td></tr>
    <tr><td>Babar Hayat</td><td>HKG</td><td><a href="/ci/engine/player/539305.html?class=3;type=allround">Twenty20 Internationals player</a> (2014 - 2026, 79 matches)</td></tr>
    <tr><td>Babar Official</td><td>PAK</td><td><a href="/ci/engine/player/1.html?class=3;type=allround">Twenty20 Internationals official</a> (2020 - 2021)</td></tr>
    </table>
    """
    source, _, client = await call_with_source(
        {
            player_search_url("Babar+Azam"): html,
            player_search_url("Babar"): html,
            player_search_url("No+Such"): "<html></html>",
        }
    )

    async with client:
        unique = await client.call_tool("find_player", {"name": "Babar Azam", "format": "T20I"})
        ambiguous = await client.call_tool("find_player", {"name": "Babar", "format": "T20I"})
        unknown = await client.call_tool("find_player", {"name": "No Such", "format": "T20I"})

    assert unique.structured_content["status"] == "match"
    assert unique.structured_content["match"]["id"] == 348144
    assert unique.structured_content["match"]["country"] == ["PAK"]
    assert unique.structured_content["match"]["formats"] == [
        {
            "class": 3,
            "label": "Twenty20 Internationals player",
            "role": "player",
            "span": "2016-2026",
            "matches": 145,
        }
    ]
    assert ambiguous.structured_content["status"] == "needs_clarification"
    assert [candidate["id"] for candidate in ambiguous.structured_content["candidates"]] == [
        348144,
        539305,
    ]
    assert unknown.structured_content == {
        "status": "not_found",
        "query": "No Such",
        "candidates": [],
    }
    assert "348144" in unique.content[0].text
    assert "539305" in ambiguous.content[0].text
    assert len(source.requests) == 3


async def test_find_player_without_format_fetches_once_and_merges_formats() -> None:
    html = """
    <table>
    <tr><td>Dual Player</td><td>AAA</td><td>
      <a href="/ci/engine/player/10.html?class=3;type=allround">Twenty20 Internationals player</a> (2020 - 2021, 2 matches)
      <a href="/ci/engine/player/10.html?class=6;type=allround">Twenty20 matches player</a> (2019 - 2022, 40 matches)
    </td></tr>
    </table>
    """
    source, _, client = await call_with_source(
        {
            player_search_url("Dual+Player"): html,
            player_search_url("Missing+Player"): "<html></html>",
        }
    )

    async with client:
        found = await client.call_tool("find_player", {"name": "Dual Player"})
        missing = await client.call_tool("find_player", {"name": "Missing Player"})

    assert source.requests == [
        player_search_url("Dual+Player"),
        player_search_url("Missing+Player"),
    ]
    assert found.structured_content["status"] == "match"
    assert [item["class"] for item in found.structured_content["match"]["formats"]] == [3, 6]
    assert missing.structured_content["status"] == "not_found"


async def test_find_player_t20_means_t20i_and_country_filters() -> None:
    html = """
    <table>
    <tr><td>Babar Azam</td><td>PAK</td><td><a href="/ci/engine/player/348144.html?class=3;type=allround">Twenty20 Internationals player</a> (2016 - 2026, 145 matches)</td></tr>
    <tr><td>Babar Hayat</td><td>HKG</td><td><a href="/ci/engine/player/539305.html?class=3;type=allround">Twenty20 Internationals player</a> (2014 - 2026, 79 matches)</td></tr>
    </table>
    """
    _, _, client = await call_with_source({player_search_url("Babar"): html})

    async with client:
        result = await client.call_tool(
            "find_player", {"name": "Babar", "format": "t20", "country": "Hong Kong"}
        )

    assert result.structured_content["status"] == "match"
    assert result.structured_content["match"]["id"] == 539305


async def test_find_player_country_fallback_needs_clarification_not_match() -> None:
    html = """
    <table>
    <tr><td>Babar Azam</td><td>PAK</td><td><a href="/ci/engine/player/348144.html?class=3;type=allround">Twenty20 Internationals player</a> (2016 - 2026, 145 matches)</td></tr>
    <tr><td>Babar Hayat</td><td>HKG</td><td><a href="/ci/engine/player/539305.html?class=3;type=allround">Twenty20 Internationals player</a> (2014 - 2026, 79 matches)</td></tr>
    </table>
    """
    _, _, client = await call_with_source({player_search_url("Babar"): html})

    async with client:
        result = await client.call_tool(
            "find_player", {"name": "Babar", "format": "T20I", "country": "India"}
        )

    assert result.structured_content["status"] == "needs_clarification"
    assert "No candidate matched country 'India'" in result.structured_content["note"]
    assert [candidate["id"] for candidate in result.structured_content["candidates"]] == [
        348144,
        539305,
    ]


async def test_find_player_country_filter_sees_candidates_beyond_top_five() -> None:
    html = """
    <table>
    <tr><td>Babar One</td><td>AAA</td><td><a href="/ci/engine/player/1.html?class=6;type=allround">Twenty20 matches player</a> (2020 - 2021, 50 matches)</td></tr>
    <tr><td>Babar Two</td><td>BBB</td><td><a href="/ci/engine/player/2.html?class=6;type=allround">Twenty20 matches player</a> (2020 - 2021, 49 matches)</td></tr>
    <tr><td>Babar Three</td><td>CCC</td><td><a href="/ci/engine/player/3.html?class=6;type=allround">Twenty20 matches player</a> (2020 - 2021, 48 matches)</td></tr>
    <tr><td>Babar Four</td><td>DDD</td><td><a href="/ci/engine/player/4.html?class=6;type=allround">Twenty20 matches player</a> (2020 - 2021, 47 matches)</td></tr>
    <tr><td>Babar Five</td><td>EEE</td><td><a href="/ci/engine/player/5.html?class=6;type=allround">Twenty20 matches player</a> (2020 - 2021, 46 matches)</td></tr>
    <tr><td>Fahad Babar</td><td>USA</td><td><a href="/ci/engine/player/6.html?class=6;type=allround">Twenty20 matches player</a> (2020 - 2021, 10 matches)</td></tr>
    </table>
    """
    _, _, client = await call_with_source({player_search_url("Babar"): html})

    async with client:
        usa = await client.call_tool(
            "find_player", {"name": "Babar", "format": "all T20", "country": "USA"}
        )
        india = await client.call_tool(
            "find_player", {"name": "Babar", "format": "all T20", "country": "India"}
        )

    assert usa.structured_content["status"] == "match"
    assert usa.structured_content["match"]["id"] == 6
    assert india.structured_content["status"] == "needs_clarification"
    assert "No candidate matched country 'India'" in india.structured_content["note"]


async def test_find_player_fetcher_errors_are_tool_errors() -> None:
    url = player_search_url("Blocked")
    _, _, client = await call_with_source(
        {url: FetchResponse(url=url, status_code=404, headers={}, text="<html>Statsguru</html>")}
    )

    async with client:
        result = await client.call_tool("find_player", {"name": "Blocked"})

    assert result.is_error is True
    assert "unavailable" in result.content[0].text


async def test_find_player_refetches_cached_lookup_miss_once() -> None:
    url = player_search_url("Late+Player")
    fresh = """
    <table>
    <tr><td>Late Player</td><td>AAA</td><td><a href="/ci/engine/player/99.html?class=3;type=allround">Twenty20 Internationals player</a> (2020 - 2021, 2 matches)</td></tr>
    </table>
    """
    source = source_for({url: "<html></html>"})
    clock = FakeClock()
    fetcher = Fetcher(settings(), clock=clock, page_source=source)
    client = Client(create_server(settings(), fetcher=fetcher))

    async with client:
        missing = await client.call_tool("find_player", {"name": "Late Player", "format": "T20I"})
        source.pages[url] = fresh
        found = await client.call_tool("find_player", {"name": "Late Player", "format": "T20I"})

    assert missing.structured_content["status"] == "not_found"
    assert found.structured_content["match"]["id"] == 99
    assert source.requests == [url, url]


async def test_query_stats_fetch_returns_exact_rows_columns_total_link_and_label() -> None:
    query = StatsguruQuery(
        **{
            "class": 2,
            "type": "batting",
            "qualifications": (Qualification(field="hundreds", minimum=10),),
            "orderby": "hundreds",
        }
    )
    url = results_url(query)
    source, _, client = await call_with_source(
        {url: result_page([row(348144, "Babar Azam", "PAK", 6626)], total=64)}
    )

    async with client:
        result = await client.call_tool(
            "query_stats",
            {
                "query": {
                    "class": 2,
                    "type": "batting",
                    "qualval1": "hundreds",
                    "qualmin1": 10,
                    "orderby": "hundreds",
                }
            },
        )

    assert source.requests == [url]
    assert result.structured_content["columns"] == [
        "Player",
        "Span",
        "Mat",
        "Runs",
        "HS",
        "Ave",
        "100",
    ]
    assert result.structured_content["rows"] == [
        {
            "Player": "Babar Azam (PAK)",
            "Span": "2015-2026",
            "Mat": 10,
            "Runs": 6626,
            "HS": "101*",
            "Ave": "50.00",
            "100": 1,
        }
    ]
    assert result.structured_content["total"] == 64
    assert result.structured_content["link"] == url
    assert "text_required_columns" not in result.structured_content
    assert "ODI batting" in result.structured_content["label"]
    assert "at least 10 hundreds" in result.structured_content["label"]
    assert result.structured_content["freshness"].startswith(
        "Freshness: newest match Statsguru included is Example XI v Sample XI"
    )
    assert "| Player" in result.content[0].text
    assert "Babar Azam (PAK)" in result.content[0].text
    assert f"Pinned link: [{result.structured_content['label']}]({url})" in result.content[0].text


async def test_query_stats_text_table_includes_sort_and_qualification_columns() -> None:
    query = StatsguruQuery(
        **{
            "class": 2,
            "type": "batting",
            "orderby": "hundreds",
            "qualval1": "hundreds",
            "qualmin1": 10,
        }
    )
    source, _, client = await call_with_source({results_url(query): detailed_batting_page()})

    async with client:
        result = await client.call_tool(
            "query_stats",
            {
                "query": {
                    "class": 2,
                    "type": "batting",
                    "orderby": "hundreds",
                    "qualval1": "hundreds",
                    "qualmin1": 10,
                }
            },
        )

    text = result.content[0].text
    header = next(line for line in text.splitlines() if line.startswith("| Player"))
    assert "100" in header
    assert "(0 more row(s), 4 more column(s) omitted.)" in text


@pytest.mark.parametrize(
    ("query_args", "headers", "row_values", "required_column"),
    [
        (
            {"class": 1, "type": "batting", "orderby": "fifty_plus"},
            ("Player", "Span", "Mat", "Inns", "NO", "Runs", "HS", "Ave", "100", "50", "0"),
            ("A Batter (AAA)", "2020-2026", "5", "4", "1", "250", "101", "83.33", "1", "2", "0"),
            "50",
        ),
        (
            {"class": 3, "type": "bowling", "orderby": "five_wickets"},
            (
                "Player",
                "Span",
                "Mat",
                "Inns",
                "Overs",
                "Mdns",
                "Runs",
                "Wkts",
                "BBI",
                "Ave",
                "Econ",
                "SR",
                "4",
                "5",
            ),
            (
                "B Bowler (BBB)",
                "2021-2026",
                "6",
                "6",
                "24.5",
                "2",
                "150",
                "9",
                "4/22",
                "16.66",
                "6.04",
                "16.5",
                "1",
                "0",
            ),
            "5",
        ),
        (
            {"class": 1, "type": "fielding", "orderby": "max_dismissals"},
            ("Player", "Span", "Mat", "Inns", "Dis", "Ct", "St", "Ct Wk", "Ct Fi", "MD", "D/I"),
            (
                "C Keeper (CCC)",
                "2019-2026",
                "7",
                "10",
                "18",
                "15",
                "3",
                "12",
                "3",
                "5 (4ct 1st)",
                "1.800",
            ),
            "MD",
        ),
        (
            {"class": 1, "type": "allround", "orderby": "allround_average"},
            (
                "Player",
                "Span",
                "Mat",
                "Runs",
                "HS",
                "Bat Av",
                "100",
                "Wkts",
                "BBI",
                "Bowl Av",
                "5",
                "Ct",
                "St",
                "Ave Diff",
            ),
            (
                "D Allrounder (DDD)",
                "2018-2026",
                "8",
                "400",
                "99",
                "40.00",
                "1",
                "20",
                "5/30",
                "22.50",
                "2",
                "12",
                "0",
                "17.50",
            ),
            "Ave Diff",
        ),
        (
            {"class": 1, "type": "fow", "orderby": "fow_fifty_plus"},
            ("Partners", "Span", "Inns", "NO", "Runs", "High", "Ave", "100", "50"),
            ("E Opener, Z Opener (EEE)", "2022-2026", "9", "1", "700", "199", "87.50", "2", "3"),
            "50",
        ),
        (
            {"class": 1, "type": "team", "orderby": "runs_per_over"},
            (
                "Team",
                "Span",
                "Mat",
                "Won",
                "Lost",
                "Tied",
                "Draw",
                "Runs",
                "Wkts",
                "Balls",
                "Ave",
                "RPO",
                "Inns",
                "HS",
                "LS",
            ),
            (
                "Example XI",
                "2020-2026",
                "10",
                "6",
                "3",
                "0",
                "1",
                "2000",
                "70",
                "1500",
                "31.25",
                "8.10",
                "10",
                "250",
                "90",
            ),
            "RPO",
        ),
        (
            {"class": 1, "type": "aggregate", "orderby": "runs_per_over"},
            ("Span", "Mat", "Won", "Tied", "Draw", "Runs", "Wkts", "Balls", "Ave", "RPO"),
            ("1877-2026", "11", "8", "1", "2", "3000", "100", "6500", "30.00", "2.76"),
            "RPO",
        ),
    ],
)
async def test_query_stats_text_table_includes_sorted_column_for_every_type(
    query_args: dict[str, object],
    headers: Sequence[str],
    row_values: Sequence[str],
    required_column: str,
) -> None:
    query = StatsguruQuery(**query_args)
    source, _, client = await call_with_source(
        {results_url(query): sorted_column_page(headers, row_values)}
    )

    async with client:
        result = await client.call_tool("query_stats", {"query": query_args})

    assert source.requests == [results_url(query)]
    header = next(line for line in result.content[0].text.splitlines() if line.startswith("| "))
    assert required_column in header


async def test_query_stats_limit_default_maximum_and_over_limit_error() -> None:
    query = StatsguruQuery(**{"class": 2, "type": "batting"})
    url1 = results_url(query)
    url200 = results_url(StatsguruQuery(**{"class": 2, "type": "batting", "size": 200}))
    rows1 = [row(index, f"Player {index}", "AAA", index) for index in range(1, 201)]
    source, _, client = await call_with_source(
        {
            url1: result_page(rows1, page=1, pages=3, total=520),
            url200: result_page(rows1, page=1, pages=3, total=520),
        }
    )

    async with client:
        default = await client.call_tool("query_stats", {"query": {"class": 2, "type": "batting"}})
        maximum = await client.call_tool(
            "query_stats", {"query": {"class": 2, "type": "batting"}, "limit": 200}
        )
        over = await client.call_tool(
            "query_stats", {"query": {"class": 2, "type": "batting"}, "limit": 201}
        )

    assert len(default.structured_content["rows"]) == 50
    assert len(maximum.structured_content["rows"]) == 200
    assert over.is_error is True
    assert "limit must be from 1 to 200" in over.content[0].text
    assert source.requests == [url1, url200]


async def test_query_stats_starts_at_requested_page_and_does_not_duplicate_it() -> None:
    query = StatsguruQuery(**{"class": 2, "type": "batting", "page": 2, "size": 200})
    page2_url = results_url(query)
    page2_rows = [row(index, f"Player {index}", "AAA", index) for index in range(201, 401)]
    source, _, client = await call_with_source(
        {page2_url: result_page(page2_rows, page=2, pages=3, total=520)}
    )

    async with client:
        result = await client.call_tool(
            "query_stats",
            {"query": {"class": 2, "type": "batting", "page": 2, "size": 200}, "limit": 200},
        )

    assert len(result.structured_content["rows"]) == 200
    assert result.structured_content["rows"][0]["Player"] == "Player 201 (AAA)"
    assert source.requests == [page2_url]


async def test_query_stats_page_two_limit_above_size_preserves_row_offsets() -> None:
    query = StatsguruQuery(**{"class": 2, "type": "batting", "page": 2})
    page2_url = results_url(query)
    page3_url = results_url(query, page=3)
    source, _, client = await call_with_source(
        {
            page2_url: result_page(
                [row(index, f"Player {index}", "AAA", index) for index in range(51, 101)],
                page=2,
                pages=4,
                total=200,
            ),
            page3_url: result_page(
                [row(index, f"Player {index}", "AAA", index) for index in range(101, 151)],
                page=3,
                pages=4,
                total=200,
            ),
        }
    )

    async with client:
        result = await client.call_tool(
            "query_stats", {"query": {"class": 2, "type": "batting", "page": 2}, "limit": 100}
        )

    assert len(result.structured_content["rows"]) == 100
    assert result.structured_content["rows"][0]["Player"] == "Player 51 (AAA)"
    assert result.structured_content["rows"][-1]["Player"] == "Player 150 (AAA)"
    assert source.requests == [page2_url, page3_url]


async def test_query_stats_fetches_complete_multipage_result_under_cap() -> None:
    query = StatsguruQuery(**{"class": 2, "type": "batting", "size": 10})
    pages = {
        results_url(query, page=page): result_page(
            [
                row(index, f"Player {index}", "AAA", index)
                for index in range((page - 1) * 10 + 1, min(page * 10, 35) + 1)
            ],
            page=page,
            pages=4,
            total=35,
        )
        for page in range(1, 5)
    }
    source, _, client = await call_with_source(pages)

    async with client:
        result = await client.call_tool(
            "query_stats", {"query": {"class": 2, "type": "batting", "size": 10}}
        )

    assert result.is_error is False
    assert len(result.structured_content["rows"]) == 35
    assert source.requests == [results_url(query, page=page) for page in range(1, 5)]


async def test_query_stats_refuses_over_cap_query_after_first_page() -> None:
    query = StatsguruQuery(**{"class": 2, "type": "batting", "size": 10})
    url = results_url(query)
    source, _, client = await call_with_source(
        {
            url: result_page(
                [row(index, f"Player {index}", "AAA", index) for index in range(1, 11)],
                page=1,
                pages=6,
                total=60,
            )
        }
    )

    async with client:
        result = await client.call_tool(
            "query_stats", {"query": {"class": 2, "type": "batting", "size": 10}}
        )

    assert result.is_error is True
    assert "needs 5 fetched pages" in result.content[0].text
    assert source.requests == [url]


async def test_query_stats_max_pages_one_allows_one_row_complete_result() -> None:
    query = StatsguruQuery(**{"class": 2, "type": "batting", "size": 10})
    url = results_url(query)
    source, _, client = await call_with_source(
        {url: result_page([row(1, "Only Player", "AAA", 1)], page=1, pages=1, total=1)},
        settings(max_pages=1),
    )

    async with client:
        result = await client.call_tool(
            "query_stats", {"query": {"class": 2, "type": "batting", "size": 10}}
        )

    assert result.is_error is False
    assert result.structured_content["total"] == 1
    assert source.requests == [url]


async def test_query_stats_fetch_false_makes_no_request() -> None:
    source, _, client = await call_with_source({})

    async with client:
        result = await client.call_tool(
            "query_stats",
            {"query": {"class": 2, "type": "batting"}, "fetch": False},
        )

    assert source.requests == []
    assert result.structured_content["fetch"] is False
    assert result.structured_content["rows"] == []
    assert result.structured_content["columns"] == []
    assert result.structured_content["link"] == results_url(
        StatsguruQuery(**{"class": 2, "type": "batting"})
    )


async def test_query_stats_no_records_returns_zero_without_none_text() -> None:
    query = StatsguruQuery(**{"class": 2, "type": "batting", "team": 1, "opposition": 1})
    source, _, client = await call_with_source({results_url(query): no_records_page()})

    async with client:
        result = await client.call_tool(
            "query_stats",
            {"query": {"class": 2, "type": "batting", "team": 1, "opposition": 1}},
        )

    assert source.requests == [results_url(query)]
    assert result.structured_content["total"] == 0
    assert result.structured_content["rows"] == []
    assert "None" not in result.content[0].text


async def test_query_stats_displays_overs_as_statsguru_text() -> None:
    query = StatsguruQuery(**{"class": 3, "type": "bowling", "orderby": "wickets"})
    source, _, client = await call_with_source({results_url(query): bowling_page()})

    async with client:
        result = await client.call_tool(
            "query_stats", {"query": {"class": 3, "type": "bowling", "orderby": "wickets"}}
        )

    assert source.requests == [results_url(query)]
    assert result.structured_content["rows"][0]["Overs"] == "449.5"
    assert "449.5" in result.content[0].text


async def test_query_stats_displays_team_rows_without_player_metadata() -> None:
    query = StatsguruQuery(**{"class": 3, "type": "team", "orderby": "won"})
    source, _, client = await call_with_source({results_url(query): team_page()})

    async with client:
        result = await client.call_tool(
            "query_stats", {"query": {"class": 3, "type": "team", "orderby": "won"}}
        )

    assert source.requests == [results_url(query)]
    assert result.structured_content["columns"] == [
        "Team",
        "Span",
        "Mat",
        "Won",
        "Lost",
        "Tied",
        "NR",
        "W/L",
        "Ave",
        "RPO",
        "Inns",
        "HS",
        "LS",
    ]
    assert result.structured_content["rows"] == [
        {
            "Team": "Example XI",
            "Span": "2020-2026",
            "Mat": 14,
            "Won": 9,
            "Lost": 4,
            "Tied": 0,
            "NR": 1,
            "W/L": "2.250",
            "Ave": "32.10",
            "RPO": "7.65",
            "Inns": 14,
            "HS": 210,
            "LS": 80,
        }
    ]
    assert "player_id" not in result.structured_content["rows"][0]
    assert "| Team" in result.content[0].text


async def test_query_stats_too_broad_and_validation_errors_are_clear_tool_errors() -> None:
    query = StatsguruQuery(**{"class": 2, "type": "batting", "size": 10})
    source, _, client = await call_with_source(
        {
            results_url(query): result_page(
                [row(index, f"A{index}", "AAA", index) for index in range(1, 11)],
                pages=2,
                total=20,
            )
        },
        settings(max_pages=1),
    )

    async with client:
        broad = await client.call_tool(
            "query_stats", {"query": {"class": 2, "type": "batting", "size": 10}, "limit": 20}
        )
        invalid = await client.call_tool(
            "query_stats", {"query": {"class": 2, "type": "batting", "view": "not-a-view"}}
        )

    assert source.requests == [results_url(query)]
    assert broad.is_error is True
    assert "too broad" in broad.content[0].text
    assert "needs 2 fetched pages" in broad.content[0].text
    assert "limit is 1" in broad.content[0].text
    assert invalid.is_error is True
    assert "validation error" in invalid.content[0].text
    assert "query.view" in invalid.content[0].text
    assert "not-a-view" in invalid.content[0].text


async def test_query_stats_limit_zero_is_a_tool_error() -> None:
    _, _, client = await call_with_source({})

    async with client:
        result = await client.call_tool(
            "query_stats", {"query": {"class": 2, "type": "batting"}, "limit": 0}
        )

    assert result.is_error is True
    assert "limit must be from 1 to 200" in result.content[0].text


async def test_tool_call_budgets_are_below_client_timeout() -> None:
    import crickey.server as server_module

    assert server_module.FETCH_BUDGET_SECONDS == 240.0
    assert server_module.FIND_PLAYER_BUDGET_SECONDS == 60.0


async def test_tool_call_budgets_are_passed_to_fetcher_call() -> None:
    player_html = """
    <table>
    <tr><td>Babar Azam</td><td>PAK</td><td><a href="/ci/engine/player/348144.html?class=3;type=allround">Twenty20 Internationals player</a> (2016 - 2026, 145 matches)</td></tr>
    </table>
    """
    query = StatsguruQuery(**{"class": 2, "type": "batting"})
    source = source_for(
        {
            player_search_url("Babar+Azam"): player_html,
            results_url(query): result_page([row(1, "A", "AAA", 1)]),
        }
    )
    clock = FakeClock()
    fetcher = RecordingFetcher(settings(), clock=clock, page_source=source)
    client = Client(create_server(settings(), fetcher=fetcher))

    async with client:
        await client.call_tool("find_player", {"name": "Babar Azam", "format": "T20I"})
        await client.call_tool("query_stats", {"query": {"class": 2, "type": "batting"}})

    assert fetcher.budgets == [60.0, 240.0]


async def test_query_stats_uses_fetcher_settings_for_page_cap() -> None:
    query = StatsguruQuery(**{"class": 2, "type": "batting", "size": 10})
    source = source_for(
        {
            results_url(query): result_page(
                [row(index, f"Player {index}", "AAA", index) for index in range(1, 11)],
                page=1,
                pages=2,
                total=20,
            ),
            results_url(query, page=2): result_page(
                [row(index, f"Player {index}", "AAA", index) for index in range(11, 21)],
                page=2,
                pages=2,
                total=20,
            ),
        }
    )
    clock = FakeClock()
    fetcher = Fetcher(settings(max_pages=2), clock=clock, page_source=source)
    client = Client(create_server(settings(max_pages=1), fetcher=fetcher))

    async with client:
        result = await client.call_tool(
            "query_stats", {"query": {"class": 2, "type": "batting", "size": 10}, "limit": 20}
        )

    assert result.is_error is False
    assert len(result.structured_content["rows"]) == 20


async def test_query_stats_d17_freshness_uses_query_end_date() -> None:
    settled = StatsguruQuery(
        **{
            "class": 2,
            "type": "batting",
            "period": {"start": "2020-01-01", "end": "2020-12-31"},
        }
    )
    recent = StatsguruQuery(
        **{
            "class": 2,
            "type": "batting",
            "period": {"start": "2026-10-01", "end": "2026-10-04"},
        }
    )

    assert _freshness_for_query(settled, FakeClock().now().date()) == Freshness.SETTLED
    assert _freshness_for_query(recent, FakeClock().now().date()) == Freshness.RECENT


async def test_query_stats_passes_d17_freshness_to_fetcher_cache() -> None:
    settled_query = StatsguruQuery(
        **{
            "class": 2,
            "type": "batting",
            "period": {"start": "2020-01-01", "end": "2020-12-31"},
        }
    )
    recent_query = StatsguruQuery(
        **{
            "class": 3,
            "type": "batting",
            "period": {"start": "2026-10-01", "end": "2026-10-04"},
        }
    )
    source, clock, client = await call_with_source(
        {
            results_url(settled_query): result_page([row(1, "Settled", "AAA", 1)]),
            results_url(recent_query): result_page([row(2, "Recent", "BBB", 2)]),
        },
        settings(recent_ttl=timedelta(seconds=10)),
    )

    async with client:
        await client.call_tool(
            "query_stats",
            {
                "query": {
                    "class": 2,
                    "type": "batting",
                    "period": {"start": "2020-01-01", "end": "2020-12-31"},
                }
            },
        )
        clock.monotonic_time += 20
        await client.call_tool(
            "query_stats",
            {
                "query": {
                    "class": 2,
                    "type": "batting",
                    "period": {"start": "2020-01-01", "end": "2020-12-31"},
                }
            },
        )
        await client.call_tool(
            "query_stats",
            {
                "query": {
                    "class": 3,
                    "type": "batting",
                    "period": {"start": "2026-10-01", "end": "2026-10-04"},
                }
            },
        )
        clock.monotonic_time += 20
        await client.call_tool(
            "query_stats",
            {
                "query": {
                    "class": 3,
                    "type": "batting",
                    "period": {"start": "2026-10-01", "end": "2026-10-04"},
                }
            },
        )

    assert source.requests == [
        results_url(settled_query),
        results_url(recent_query),
        results_url(recent_query),
    ]


async def test_query_stats_uses_time_budget_for_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    import crickey.server as server_module

    monkeypatch.setattr(server_module, "FETCH_BUDGET_SECONDS", 2.0)
    query = StatsguruQuery(**{"class": 2, "type": "batting", "size": 10})
    page1 = results_url(query)
    source, clock, client = await call_with_source(
        {
            page1: result_page(
                [row(index, f"A{index}", "AAA", index) for index in range(1, 11)],
                page=1,
                pages=2,
                total=11,
            )
        },
        settings(min_interval=timedelta(seconds=3)),
    )

    async with client:
        result = await client.call_tool(
            "query_stats",
            {"query": {"class": 2, "type": "batting", "size": 10}, "limit": 11},
        )

    assert source.requests == [page1]
    assert clock.sleeps == []
    assert result.is_error is True
    assert "Not enough time left" in result.content[0].text


async def test_blocked_and_unavailable_pages_are_clear_tool_errors() -> None:
    query = StatsguruQuery(**{"class": 2, "type": "batting"})
    url = results_url(query)
    blocked_source, _, blocked_client = await call_with_source(
        {url: FetchResponse(url=url, status_code=403, headers={}, text="<html>Statsguru</html>")}
    )
    unavailable_source, _, unavailable_client = await call_with_source(
        {url: FetchResponse(url=url, status_code=404, headers={}, text="<html>Statsguru</html>")}
    )

    async with blocked_client:
        blocked = await blocked_client.call_tool(
            "query_stats", {"query": {"class": 2, "type": "batting"}}
        )
    async with unavailable_client:
        unavailable = await unavailable_client.call_tool(
            "query_stats", {"query": {"class": 2, "type": "batting"}}
        )

    assert blocked_source.requests == [url]
    assert blocked.is_error is True
    assert "blocked" in blocked.content[0].text
    assert unavailable_source.requests == [url]
    assert unavailable.is_error is True
    assert "unavailable" in unavailable.content[0].text


async def test_progress_notifications_are_sent_during_spacing_wait() -> None:
    query = StatsguruQuery(**{"class": 2, "type": "batting", "size": 10})
    url1 = results_url(query)
    url2 = results_url(query, page=2)
    source, clock, client = await call_with_source(
        {
            url1: result_page(
                [row(index, f"A{index}", "AAA", index) for index in range(1, 11)],
                page=1,
                pages=2,
                total=11,
            ),
            url2: result_page([row(11, "B", "BBB", 11)], page=2, pages=2, total=11),
        },
        settings(min_interval=timedelta(seconds=3)),
    )
    events: list[tuple[float, float | None, str | None]] = []

    async def progress(progress: float, total: float | None, message: str | None) -> None:
        events.append((progress, total, message))

    async with client:
        result = await client.call_tool(
            "query_stats",
            {"query": {"class": 2, "type": "batting", "size": 10}, "limit": 11},
            progress_callback=progress,
        )

    assert len(result.structured_content["rows"]) == 11
    assert source.requests == [url1, url2]
    assert clock.sleeps == [3.0]
    assert events == [(3.0, None, "spacing requests politely for 3 seconds")]
