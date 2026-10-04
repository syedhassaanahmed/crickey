from __future__ import annotations

# ruff: noqa: E501
import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from mcp import Client

from crickey.fetcher import Fetcher, MemoryPageSource
from crickey.metrics import batting_metric
from crickey.query import PlayerPageSpec, Qualification, ResolvedPeriod, StatsguruQuery
from crickey.server import create_server
from crickey.settings import Settings

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeClock:
    def __init__(self) -> None:
        self.monotonic_time = 0.0
        self.wall_time = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)

    def monotonic(self) -> float:
        return self.monotonic_time

    def now(self) -> datetime:
        return self.wall_time + timedelta(seconds=self.monotonic_time)

    async def sleep(self, seconds: float) -> None:
        self.monotonic_time += seconds
        await asyncio.sleep(0)


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


def url(query: StatsguruQuery) -> str:
    return query.results_url(as_of=FakeClock().now().date())


def player_search_url(name: str) -> str:
    return f"https://stats.cricinfo.com/ci/engine/stats/analysis.html?search={name.replace(' ', '+')};template=analysis"


def result_page(rows: list[str], *, page: int = 1, pages: int = 1, total: int | None = None) -> str:
    total = len(rows) if total is None else total
    start = 1 if rows else 0
    end = len(rows) if rows else 0
    if page > 1 and rows:
        start = (page - 1) * 200 + 1
        end = start + len(rows) - 1
    return f"""
    <html><body>
    <table class="engineTable"><caption>Overall figures</caption>
    <tr><th>Player</th><th>Span</th><th>Mat</th><th>Inns</th><th>NO</th><th>Runs</th><th>HS</th><th>Ave</th><th>BF</th><th>SR</th><th>100</th><th>50</th><th>0</th></tr>
    {"".join(rows)}
    </table>
    <table><tr><td>Page <b>{page}</b> of <b>{pages}</b></td><td>Showing <b>{start}</b> - <b>{end}</b> of <b>{total}</b></td></tr></table>
    <table class="engineTable"><tr class="data2"><td><b>Statsguru includes the following current or recent ODIs:</b></td></tr>
    <tr class="data2"><td>Example XI v Sample XI at Testville, 1st ODI, Oct 3, 2026 [<a href="/ci/engine/match/1.html">ODI # 1</a>]</td></tr>
    </table>
    </body></html>
    """


def row(
    player_id: int,
    name: str,
    team: str,
    span: str,
    inns: int,
    runs: int,
    ave: str,
    bf: int,
    sr: str,
    hundreds: int,
    fifties: int,
) -> str:
    return f"""
    <tr class="data1"><td><a href="/ci/content/player/{player_id}.html">{name}</a> ({team})</td><td>{span}</td><td>10</td><td>{inns}</td><td>0</td><td>{runs}</td><td>122</td><td>{ave}</td><td>{bf}</td><td>{sr}</td><td>{hundreds}</td><td>{fifties}</td><td>0</td></tr>
    """


def search_page(*rows: str) -> str:
    return "<table>" + "".join(rows) + "</table>"


def search_row(
    name: str, country: str, player_id: int, class_id: int, label: str, span: str
) -> str:
    return f'<tr><td>{name}</td><td>{country}</td><td><a href="/ci/engine/player/{player_id}.html?class={class_id};type=allround">{label} player</a> ({span}, 145 matches)</td></tr>'


def career_form(player_id: int, class_id: int, start: str, end: str) -> tuple[str, str]:
    form_url = f"https://stats.cricinfo.com/ci/engine/player/{player_id}.html?class={class_id};type=batting"
    return (
        form_url,
        f'<form name="gurumenu"><input type="hidden" name="spanmin0" value="{start}"><input type="hidden" name="spanmax0" value="{end}"></form>',
    )


def player_page(row_html: str) -> str:
    return f"""
    <html><body>
    <table class="engineTable"><caption>Career averages</caption>
    <tr><th></th><th>Span</th><th>Mat</th><th>Inns</th><th>NO</th><th>Runs</th><th>HS</th><th>Ave</th><th>BF</th><th>SR</th><th>100</th><th>50</th><th>0</th></tr>
    {row_html}
    </table></body></html>
    """


async def client_for(pages: dict[str, str], settings_: Settings | None = None):
    source = MemoryPageSource(pages)
    fetcher = Fetcher(settings_ or settings(), clock=FakeClock(), page_source=source)
    return source, Client(create_server(settings_ or settings(), fetcher=fetcher))


async def test_golden_question_1_odi_innings_per_hundred_leaderboard_requests_and_cache() -> None:
    query = StatsguruQuery(
        **{
            "class": 2,
            "type": "batting",
            "qualifications": (Qualification(field="hundreds", minimum=10),),
            "orderby": "hundreds",
            "orderbyad": "reverse",
            "size": 200,
        }
    )
    source, client = await client_for(
        {
            url(query): result_page(
                [
                    row(1, "Alpha", "AAA", "2010-2020", 80, 4000, "50.00", 4500, "88.88", 12, 20),
                    row(
                        348144,
                        "Babar Azam",
                        "PAK",
                        "2015-2026",
                        140,
                        6626,
                        "53.43",
                        7652,
                        "86.59",
                        20,
                        38,
                    ),
                    row(2, "Beta", "BBB", "2011-2021", 100, 3000, "35.00", 3600, "83.33", 10, 15),
                ],
                total=64,
            )
        }
    )

    async with client:
        cold = await client.call_tool(
            "leaderboard",
            {"format": "ODI", "metric": "innings_per_hundred", "minimum": 10, "top_n": 2},
        )
        assert len(source.requests) == 1
        source.requests.clear()
        warm = await client.call_tool(
            "leaderboard",
            {"format": "ODI", "metric": "innings_per_hundred", "minimum": 10, "top_n": 2},
        )

    assert source.requests == []
    assert cold.structured_content["rows"][0]["player"] == "Alpha"
    assert cold.structured_content["rows"][0]["value"] == "6.67"
    assert len(cold.structured_content["rows"]) == 2
    assert cold.structured_content["group"]["value"] == "7.62"
    assert warm.structured_content["answer_markdown"] == cold.structured_content["answer_markdown"]


async def test_golden_questions_2_and_3_babar_t20i_comparison_requests_and_cache() -> None:
    period = ResolvedPeriod(start="2016-09-07", end="2026-02-24")
    base_query = StatsguruQuery(
        **{
            "class": 3,
            "type": "batting",
            "period": period,
            "qualifications": (Qualification(field="runs", minimum=1000),),
            "orderby": "batting_average",
            "size": 200,
        }
    )
    proof_query = base_query.model_copy(
        update={
            "qualifications": (
                Qualification(field="runs", minimum=1000),
                Qualification(field="batting_average", minimum="38.94"),
                Qualification(field="batting_strike_rate", minimum="128.02"),
            )
        }
    )
    rows = [
        row(1001, "Karanbir Singh", "AUT", "2024-2025", 40, 1721, "47.8", 1017, "169.22", 2, 10),
        row(1002, "NT Tilak Varma", "IND", "2023-2026", 35, 1290, "44.48", 911, "141.6", 1, 9),
        row(253802, "V Kohli", "IND", "2017-2024", 70, 2531, "44.4", 1833, "138.07", 1, 20),
        row(219889, "DA Warner", "AUS", "2016-2024", 50, 1616, "41.43", 1116, "144.8", 1, 12),
        row(
            1005, "K Kadowaki-Fleming", "JPN", "2022-2025", 50, 1669, "40.7", 1150, "145.13", 1, 12
        ),
        row(1006, "Muhammad Tanveer", "QAT", "2019-2025", 55, 1980, "39.6", 1484, "133.42", 1, 15),
        row(348144, "Babar Azam", "PAK", "2016-2026", 136, 4596, "38.94", 3590, "128.02", 3, 39),
        row(2001, "Tie Player", "AAA", "2016-2026", 60, 1200, "38.94", 937, "128.02", 1, 8),
        row(2002, "Average Only", "BBB", "2016-2026", 60, 1200, "38.94", 1000, "120.00", 1, 8),
    ]
    form_url, form = career_form(348144, 3, "07 Sep 2016", "24 Feb 2026")
    source, client = await client_for(
        {
            player_search_url("Babar Azam"): search_page(
                search_row("Babar Azam", "PAK", 348144, 3, "Twenty20 Internationals", "2016 - 2026")
            ),
            form_url: form,
            url(base_query): result_page(rows, total=182),
            url(proof_query): result_page(rows[:8], total=8),
        }
    )

    async with client:
        cold = await client.call_tool(
            "better_than_player",
            {
                "player_name": "Babar Azam",
                "format": "T20I",
                "metrics": ["average", "strike_rate"],
            },
        )
        assert len(source.requests) == 4
        source.requests.clear()
        await client.call_tool(
            "better_than_player",
            {
                "player_name": "Babar Azam",
                "format": "T20I",
                "metrics": ["average", "strike_rate"],
            },
        )

    assert source.requests == []
    assert [row["player"] for row in cold.structured_content["beaters"]] == [
        "Karanbir Singh",
        "NT Tilak Varma",
        "V Kohli",
        "DA Warner",
        "K Kadowaki-Fleming",
        "Muhammad Tanveer",
    ]
    assert any(row["relation"] == "target" for row in cold.structured_content["rows"])
    assert "Tie Player" not in [row["player"] for row in cold.structured_content["beaters"]]
    assert "Average Only" not in [row["player"] for row in cold.structured_content["beaters"]]
    assert [row["player"] for row in cold.structured_content["ties"]] == ["Tie Player"]
    assert "tied with Babar Azam" in cold.structured_content["answer_markdown"]
    assert cold.structured_content["proof"]["confirmed"] is True
    assert "qualval2=batting_average" in cold.structured_content["proof"]["url"]


async def test_golden_question_2_test_hundreds_more_frequently_requests_and_cache() -> None:
    period = ResolvedPeriod(start="2016-10-13", end="2026-02-24")
    query = StatsguruQuery(
        **{
            "class": 1,
            "type": "batting",
            "period": period,
            "qualifications": (Qualification(field="hundreds", minimum=5),),
            "size": 200,
        }
    )
    form_url, form = career_form(348144, 1, "13 Oct 2016", "24 Feb 2026")
    source, client = await client_for(
        {
            player_search_url("Babar Azam"): search_page(
                search_row("Babar Azam", "PAK", 348144, 1, "Test matches", "2016 - 2026")
            ),
            form_url: form,
            url(query): result_page(
                [
                    row(
                        1,
                        "Frequent Hundred",
                        "AAA",
                        "2017-2025",
                        60,
                        3000,
                        "50.00",
                        5000,
                        "60.00",
                        12,
                        10,
                    ),
                    row(
                        348144,
                        "Babar Azam",
                        "PAK",
                        "2016-2026",
                        95,
                        4200,
                        "45.00",
                        8000,
                        "52.50",
                        10,
                        25,
                    ),
                    row(
                        2,
                        "Less Frequent",
                        "BBB",
                        "2017-2025",
                        120,
                        5000,
                        "48.00",
                        9000,
                        "55.55",
                        10,
                        20,
                    ),
                ],
                total=3,
            ),
        }
    )

    async with client:
        cold = await client.call_tool(
            "better_than_player",
            {
                "player_name": "Babar Azam",
                "format": "Test",
                "metrics": ["innings per hundred"],
            },
        )
        assert len(source.requests) == 3
        source.requests.clear()
        await client.call_tool(
            "better_than_player",
            {
                "player_name": "Babar Azam",
                "format": "Test",
                "metrics": ["innings_per_hundred"],
            },
        )

    assert source.requests == []
    assert [row["player"] for row in cold.structured_content["beaters"]] == ["Frequent Hundred"]
    assert cold.structured_content["proof"]["confirmed"] is False
    assert "innings ÷ hundreds" in cold.structured_content["answer_markdown"]


async def test_golden_question_4_last_years_test_record_requests_and_cache() -> None:
    form_url, form = career_form(348144, 1, "13 Oct 2016", "24 Feb 2026")
    spec = PlayerPageSpec(
        player_id=348144,
        **{
            "class": 1,
            "type": "batting",
            "period": {"start": "2025-02-24", "end": "2026-02-24"},
        },
    )
    source, client = await client_for(
        {
            player_search_url("Babar Azam"): search_page(
                search_row("Babar Azam", "PAK", 348144, 1, "Test matches", "2016 - 2026")
            ),
            form_url: form,
            spec.url(as_of=FakeClock().now().date()): player_page(
                '<tr class="data1"><td>unfiltered</td><td>2016-2026</td><td>60</td><td>100</td><td>5</td><td>4000</td><td>196</td><td>42.10</td><td>7000</td><td>57.14</td><td>10</td><td>25</td><td>5</td></tr>'
                '<tr class="data1"><td>filtered</td><td>2025-2026</td><td>8</td><td>14</td><td>1</td><td>650</td><td>120</td><td>50.00</td><td>1200</td><td>54.16</td><td>2</td><td>3</td><td>0</td></tr>'
            ),
        }
    )

    async with client:
        result = await client.call_tool(
            "player_record",
            {
                "player_name": "Babar Azam",
                "format": "Test",
                "period": {"kind": "last_years", "years": 1},
            },
        )
        assert len(source.requests) == 3
        source.requests.clear()
        await client.call_tool(
            "player_record",
            {
                "player_name": "Babar Azam",
                "format": "Test",
                "period": {"kind": "last_years", "years": 1},
            },
        )

    assert source.requests == []
    assert result.structured_content["row"]["Ave"] == "50.00"


async def test_golden_question_5_babar_odi_world_cup_record_requests_and_cache() -> None:
    spec = PlayerPageSpec(player_id=348144, **{"class": 2, "type": "batting", "trophy": 12})
    source, client = await client_for(
        {
            player_search_url("Babar Azam"): search_page(
                search_row("Babar Azam", "PAK", 348144, 2, "One-Day Internationals", "2015 - 2026")
            ),
            spec.url(as_of=FakeClock().now().date()): player_page(
                '<tr class="data1"><td>unfiltered</td><td>2015-2026</td><td>143</td><td>140</td><td>16</td><td>6626</td><td>158</td><td>53.43</td><td>7652</td><td>86.59</td><td>20</td><td>38</td><td>5</td></tr>'
                '<tr class="data1"><td>filtered</td><td>2019-2023</td><td>17</td><td>17</td><td>2</td><td>794</td><td>101*</td><td>52.93</td><td>926</td><td>85.74</td><td>1</td><td>7</td><td>0</td></tr>'
            ),
        }
    )

    async with client:
        result = await client.call_tool(
            "player_record",
            {"player_name": "Babar Azam", "format": "ODI", "trophy": "World Cup"},
        )
        assert len(source.requests) == 2
        source.requests.clear()
        await client.call_tool(
            "player_record",
            {"player_name": "Babar Azam", "format": "ODI", "trophy": "World Cup"},
        )

    assert source.requests == []
    assert result.structured_content["row"]["100"] == 1
    assert result.structured_content["row"]["HS"] == "101*"
    short_answer = result.structured_content["answer_markdown"].split("\n", 1)[0]
    assert "hundreds 1" in short_answer
    assert "average 52.93" in short_answer
    assert "hundreds 20" not in short_answer


async def test_player_record_without_filtered_row_reports_no_matches() -> None:
    spec = PlayerPageSpec(player_id=348144, **{"class": 2, "type": "batting", "trophy": 12})
    source, client = await client_for(
        {
            player_search_url("Babar Azam"): search_page(
                search_row("Babar Azam", "PAK", 348144, 2, "One-Day Internationals", "2015 - 2026")
            ),
            spec.url(as_of=FakeClock().now().date()): player_page(
                '<tr class="data1"><td>unfiltered</td><td>2015-2026</td><td>143</td><td>140</td><td>16</td><td>6626</td><td>158</td><td>53.43</td><td>7652</td><td>86.59</td><td>20</td><td>38</td><td>5</td></tr>'
            ),
        }
    )

    async with client:
        result = await client.call_tool(
            "player_record",
            {"player_name": "Babar Azam", "format": "ODI", "trophy": "World Cup"},
        )

    assert len(source.requests) == 2
    assert result.structured_content["status"] == "no_matches"
    assert result.structured_content["row"] is None
    assert "No matches found" in result.structured_content["answer_markdown"]


async def test_leaderboard_ties_share_rank_and_extend_top_n_boundary() -> None:
    query = StatsguruQuery(
        **{
            "class": 1,
            "type": "batting",
            "qualifications": (Qualification(field="hundreds", minimum=5),),
            "orderby": "hundreds",
            "orderbyad": "reverse",
            "size": 200,
        }
    )
    source, client = await client_for(
        {
            url(query): result_page(
                [
                    row(1, "P1", "AAA", "2000-2010", 50, 2500, "50.00", 4000, "62.50", 10, 5),
                    row(2, "P2", "BBB", "2000-2010", 50, 2500, "50.00", 4000, "62.50", 10, 5),
                    row(3, "P3", "CCC", "2000-2010", 60, 2500, "41.66", 4000, "62.50", 5, 5),
                ],
                total=3,
            )
        }
    )

    async with client:
        result = await client.call_tool(
            "leaderboard",
            {"format": "Test", "metric": "innings per hundred", "minimum": 5, "top_n": 1},
        )

    assert [row["rank"] for row in result.structured_content["rows"]] == [1, 1]
    assert [row["player"] for row in result.structured_content["rows"]] == ["P1", "P2"]
    assert "tied for the lead" in result.structured_content["answer_markdown"]


async def test_sortable_leaderboard_follows_boundary_tie_to_next_page() -> None:
    query = StatsguruQuery(
        **{
            "class": 2,
            "type": "batting",
            "qualifications": (Qualification(field="innings", minimum=20),),
            "orderby": "batting_average",
            "size": 10,
        }
    )
    page2 = query.model_copy(update={"page": 2})
    source, client = await client_for(
        {
            url(query): result_page(
                [
                    row(
                        index,
                        f"P{index}",
                        "AAA",
                        "2000-2010",
                        20,
                        1000,
                        "50.00",
                        1000,
                        "100.00",
                        1,
                        1,
                    )
                    for index in range(1, 11)
                ],
                pages=2,
                total=11,
            ),
            url(page2): result_page(
                [row(11, "P11", "AAA", "2000-2010", 20, 1000, "50.00", 1000, "100.00", 1, 1)],
                page=2,
                pages=2,
                total=11,
            ),
        }
    )

    async with client:
        result = await client.call_tool(
            "leaderboard", {"format": "ODI", "metric": "average", "top_n": 10}
        )

    assert len(source.requests) == 2
    assert len(result.structured_content["rows"]) == 11


async def test_sortable_leaderboard_boundary_tie_overflow_errors() -> None:
    query = StatsguruQuery(
        **{
            "class": 2,
            "type": "batting",
            "qualifications": (Qualification(field="innings", minimum=20),),
            "orderby": "batting_average",
            "size": 10,
        }
    )
    source, client = await client_for(
        {
            url(query): result_page(
                [
                    row(
                        index,
                        f"P{index}",
                        "AAA",
                        "2000-2010",
                        20,
                        1000,
                        "50.00",
                        1000,
                        "100.00",
                        1,
                        1,
                    )
                    for index in range(1, 11)
                ],
                pages=2,
                total=11,
            )
        },
        settings(max_pages=1),
    )

    async with client:
        result = await client.call_tool(
            "leaderboard", {"format": "ODI", "metric": "average", "top_n": 10}
        )

    assert result.is_error is True
    assert "tie at rank 10 continues" in result.content[0].text
    assert len(source.requests) == 1


async def test_derived_leaderboard_fetches_later_pages_for_ranks_and_group() -> None:
    query = StatsguruQuery(
        **{
            "class": 2,
            "type": "batting",
            "qualifications": (Qualification(field="hundreds", minimum=10),),
            "orderby": "hundreds",
            "orderbyad": "reverse",
            "size": 200,
        }
    )
    page2 = query.model_copy(update={"page": 2})
    source, client = await client_for(
        {
            url(query): result_page(
                [row(1, "Page One", "AAA", "2000-2010", 100, 4000, "40.00", 5000, "80.00", 10, 10)],
                pages=2,
                total=2,
            ),
            url(page2): result_page(
                [row(2, "Page Two", "BBB", "2000-2010", 60, 3000, "50.00", 4000, "75.00", 10, 10)],
                page=2,
                pages=2,
                total=2,
            ),
        }
    )

    async with client:
        result = await client.call_tool(
            "leaderboard",
            {"format": "ODI", "metric": "innings_per_hundred", "minimum": 10, "top_n": 1},
        )

    assert len(source.requests) == 2
    assert result.structured_content["rows"][0]["player"] == "Page Two"
    assert result.structured_content["group"]["value"] == "8.00"


async def test_better_than_any_mode_skips_confirmation_fetch() -> None:
    period = ResolvedPeriod(start="2016-09-07", end="2026-02-24")
    base_query = StatsguruQuery(
        **{
            "class": 3,
            "type": "batting",
            "period": period,
            "qualifications": (Qualification(field="runs", minimum=1000),),
            "orderby": "batting_average",
            "size": 200,
        }
    )
    form_url, form = career_form(348144, 3, "07 Sep 2016", "24 Feb 2026")
    source, client = await client_for(
        {
            player_search_url("Babar Azam"): search_page(
                search_row("Babar Azam", "PAK", 348144, 3, "Twenty20 Internationals", "2016 - 2026")
            ),
            form_url: form,
            url(base_query): result_page(
                [
                    row(
                        1,
                        "Average Beater",
                        "AAA",
                        "2019-2025",
                        50,
                        1500,
                        "40.00",
                        1500,
                        "100.00",
                        1,
                        5,
                    ),
                    row(
                        348144,
                        "Babar Azam",
                        "PAK",
                        "2016-2026",
                        136,
                        4596,
                        "38.94",
                        3590,
                        "128.02",
                        3,
                        39,
                    ),
                    row(
                        2,
                        "Strike Beater",
                        "BBB",
                        "2019-2025",
                        50,
                        1500,
                        "30.00",
                        1000,
                        "150.00",
                        1,
                        5,
                    ),
                ],
                total=3,
            ),
        }
    )

    async with client:
        result = await client.call_tool(
            "better_than_player",
            {
                "player_name": "Babar Azam",
                "format": "T20I",
                "metrics": ["average", "strike_rate"],
                "match": "any",
            },
        )

    assert len(source.requests) == 3
    assert result.structured_content["proof"]["confirmed"] is False
    assert "cannot express OR" in result.structured_content["proof"]["label"]
    assert [row["player"] for row in result.structured_content["beaters"]] == [
        "Average Beater",
        "Strike Beater",
    ]


async def test_comparison_proof_uses_only_remaining_page_allowance() -> None:
    period = ResolvedPeriod(start="2016-09-07", end="2026-02-24")
    base_query = StatsguruQuery(
        **{
            "class": 3,
            "type": "batting",
            "period": period,
            "qualifications": (Qualification(field="runs", minimum=1000),),
            "orderby": "batting_average",
            "size": 200,
        }
    )
    proof_query = base_query.model_copy(
        update={
            "qualifications": (
                Qualification(field="runs", minimum=1000),
                Qualification(field="batting_average", minimum="38.94"),
            )
        }
    )
    form_url, form = career_form(348144, 3, "07 Sep 2016", "24 Feb 2026")
    base_rows = [
        row(1, "Average Beater", "AAA", "2019-2025", 50, 1500, "40.00", 1500, "100.00", 1, 5),
        row(348144, "Babar Azam", "PAK", "2016-2026", 136, 4596, "38.94", 3590, "128.02", 3, 39),
    ]
    source, client = await client_for(
        {
            player_search_url("Babar Azam"): search_page(
                search_row("Babar Azam", "PAK", 348144, 3, "Twenty20 Internationals", "2016 - 2026")
            ),
            form_url: form,
            url(base_query): result_page(base_rows, pages=3, total=402),
            url(base_query.model_copy(update={"page": 2})): result_page(
                [row(2, "Page2", "AAA", "2019-2025", 50, 1000, "30.00", 1000, "100.00", 1, 5)],
                page=2,
                pages=3,
                total=402,
            ),
            url(base_query.model_copy(update={"page": 3})): result_page(
                [row(3, "Page3", "AAA", "2019-2025", 50, 1000, "20.00", 1000, "100.00", 1, 5)],
                page=3,
                pages=3,
                total=402,
            ),
            url(proof_query): result_page(base_rows[:1], pages=2, total=2),
            url(proof_query.model_copy(update={"page": 2})): result_page(
                base_rows[1:],
                page=2,
                pages=2,
                total=2,
            ),
        },
        settings(max_pages=4),
    )

    async with client:
        result = await client.call_tool(
            "better_than_player",
            {"player_name": "Babar Azam", "format": "T20I", "metrics": ["average"]},
        )

    assert result.structured_content["proof"]["confirmed"] is False
    assert (
        "confirmation needs 2 proof pages, over the 1-page limit"
        in result.structured_content["proof"]["label"]
    )
    assert url(proof_query.model_copy(update={"page": 2})) not in source.requests


async def test_sortable_metric_leaderboard_fetches_one_page_when_query_is_broad() -> None:
    query = StatsguruQuery(
        **{
            "class": 2,
            "type": "batting",
            "qualifications": (Qualification(field="innings", minimum=20),),
            "orderby": "batting_average",
            "size": 10,
        }
    )
    source, client = await client_for(
        {
            url(query): result_page(
                [
                    row(
                        index,
                        f"P{index}",
                        "AAA",
                        "2000-2010",
                        20,
                        1000,
                        f"{60 - index:.2f}",
                        1000,
                        "100.00",
                        1,
                        1,
                    )
                    for index in range(1, 11)
                ],
                pages=120,
                total=1200,
            )
        },
        settings(max_pages=1),
    )

    async with client:
        result = await client.call_tool(
            "leaderboard", {"format": "ODI", "metric": "average", "top_n": 3}
        )

    assert result.is_error is False
    assert len(source.requests) == 1
    assert len(result.structured_content["rows"]) == 3
    assert "Fetched 1 sorted Statsguru result page" in result.structured_content["answer_markdown"]


async def test_answer_tools_clarification_unsupported_ties_and_too_broad() -> None:
    source, client = await client_for(
        {
            player_search_url("Babar"): search_page(
                search_row(
                    "Babar Azam", "PAK", 348144, 3, "Twenty20 Internationals", "2016 - 2026"
                ),
                search_row(
                    "Babar Hayat", "HKG", 539305, 3, "Twenty20 Internationals", "2014 - 2026"
                ),
            )
        }
    )
    async with client:
        clarify = await client.call_tool(
            "better_than_player", {"player_name": "Babar", "format": "T20I", "metrics": ["runs"]}
        )
        unsupported = await client.call_tool(
            "leaderboard", {"format": "Test", "metric": "strike_rate"}
        )

    assert clarify.structured_content["status"] == "needs_clarification"
    assert unsupported.is_error is True
    assert "not supported" in unsupported.content[0].text
    assert len(source.requests) == 1

    broad_query = StatsguruQuery(
        **{
            "class": 2,
            "type": "batting",
            "qualifications": (Qualification(field="hundreds", minimum=1),),
            "orderby": "hundreds",
            "orderbyad": "reverse",
            "size": 200,
        }
    )
    broad_source, broad_client = await client_for(
        {
            url(broad_query): result_page(
                [row(1, "Alpha", "AAA", "2010-2020", 10, 1000, "50.00", 1000, "100.00", 1, 1)],
                pages=2,
                total=201,
            )
        },
        settings(max_pages=1),
    )
    async with broad_client:
        broad = await broad_client.call_tool(
            "leaderboard", {"format": "ODI", "metric": "innings_per_hundred", "minimum": 1}
        )
    assert broad.is_error is True
    assert "too broad" in broad.content[0].text
    assert len(broad_source.requests) == 1


async def test_filter_clarification_text_shows_lookup_candidates() -> None:
    form_url = (
        "https://stats.cricinfo.com/ci/engine/stats/index.html?class=1;filter=advanced;type=batting"
    )
    source, client = await client_for(
        {
            form_url: """
            <form name="gurumenu">
            <select name="ground">
            <option value="701">UAE: Dubai Sports City Cricket Stadium</option>
            <option value="702">UAE: ICC Academy, Dubai</option>
            </select>
            </form>
            """
        }
    )

    async with client:
        result = await client.call_tool(
            "leaderboard",
            {"format": "Test", "metric": "innings_per_hundred", "ground": "Dubai"},
        )

    assert result.structured_content["status"] == "needs_clarification"
    assert "Dubai Sports City Cricket Stadium" in result.content[0].text
    assert "ICC Academy, Dubai" in result.content[0].text
    assert source.requests == [form_url]


async def test_period_without_kind_is_rejected_with_available_kinds() -> None:
    source, client = await client_for({})

    async with client:
        result = await client.call_tool(
            "player_record",
            {
                "player_name": "Babar Azam",
                "format": "Test",
                "period": {"years": 2},
            },
        )

    assert source.requests == []
    assert result.is_error is True
    assert "period.kind is required" in result.content[0].text
    assert "all_time" in result.content[0].text
    assert "last_years" in result.content[0].text


def test_issue_9_review_follow_up_innings_per_hundred_precision() -> None:
    metric = batting_metric("innings_per_hundred")

    assert metric.display_value(Decimal(100) / Decimal(14)) == Decimal("7.14")


async def test_issue_9_review_follow_up_punctuation_matching_and_refetch_only_on_miss() -> None:
    source, client = await client_for(
        {
            player_search_url("Kadowaki Fleming"): search_page(
                search_row(
                    "K Kadowaki-Fleming",
                    "JPN",
                    1005,
                    3,
                    "Twenty20 Internationals",
                    "2022 - 2025",
                )
            ),
            player_search_url("Babar"): search_page(
                search_row("Babar Azam", "PAK", 348144, 3, "Twenty20 Internationals", "2016 - 2026")
            ),
            player_search_url("Missing"): "<html></html>",
        }
    )

    async with client:
        punctuation = await client.call_tool(
            "find_player", {"name": "Kadowaki Fleming", "format": "T20I"}
        )
        first = await client.call_tool("find_player", {"name": "Babar", "format": "T20I"})
        source.pages[player_search_url("Babar")] = "<html></html>"
        second = await client.call_tool("find_player", {"name": "Babar", "format": "T20I"})
        missing = await client.call_tool("find_player", {"name": "Missing", "format": "T20I"})

    assert punctuation.structured_content["match"]["id"] == 1005
    assert first.structured_content["status"] == "match"
    assert second.structured_content["status"] == "match"
    assert missing.structured_content["status"] == "not_found"
    assert source.requests == [
        player_search_url("Kadowaki Fleming"),
        player_search_url("Babar"),
        player_search_url("Missing"),
    ]
