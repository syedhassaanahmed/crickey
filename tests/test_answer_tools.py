from __future__ import annotations

# ruff: noqa: E501
import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from mcp import Client

from crickey.fetcher import Fetcher, MemoryPageSource
from crickey.metrics import BATTING_METRICS, BOWLING_METRICS, batting_metric, bowling_metric
from crickey.query import PlayerPageSpec, Qualification, ResolvedPeriod, StatsguruQuery
from crickey.server import _comparison_default_minimum, create_server
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


def bowling_result_page(
    rows: list[str], *, page: int = 1, pages: int = 1, total: int | None = None
) -> str:
    total = len(rows) if total is None else total
    start = 1 if rows else 0
    end = len(rows) if rows else 0
    if page > 1 and rows:
        start = (page - 1) * 200 + 1
        end = start + len(rows) - 1
    return f"""
    <html><body>
    <table class="engineTable"><caption>Overall figures</caption>
    <tr><th>Player</th><th>Span</th><th>Mat</th><th>Inns</th><th>Balls</th><th>Overs</th><th>Mdns</th><th>Runs</th><th>Wkts</th><th>BBI</th><th>BBM</th><th>Ave</th><th>Econ</th><th>SR</th><th>4</th><th>5</th><th>10</th></tr>
    {"".join(rows)}
    </table>
    <table><tr><td>Page <b>{page}</b> of <b>{pages}</b></td><td>Showing <b>{start}</b> - <b>{end}</b> of <b>{total}</b></td></tr></table>
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


def bowling_row(
    player_id: int,
    name: str,
    team: str,
    span: str,
    wickets: int,
    ave: str,
    econ: str = "2.50",
    sr: str = "50.0",
    five: int = 5,
    ten: int = 1,
) -> str:
    return f"""
    <tr class="data1"><td><a href="/ci/content/player/{player_id}.html">{name}</a> ({team})</td><td>{span}</td><td>50</td><td>80</td><td>6000</td><td>1000.0</td><td>200</td><td>2500</td><td>{wickets}</td><td>6/40</td><td>10/100</td><td>{ave}</td><td>{econ}</td><td>{sr}</td><td>10</td><td>{five}</td><td>{ten}</td></tr>
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


def bowling_player_page(row_html: str) -> str:
    return f"""
    <html><body>
    <table class="engineTable"><caption>Career averages</caption>
    <tr><th></th><th>Span</th><th>Mat</th><th>Inns</th><th>Overs</th><th>Mdns</th><th>Runs</th><th>Wkts</th><th>BBI</th><th>Ave</th><th>Econ</th><th>SR</th><th>4</th><th>5</th><th>10</th></tr>
    {row_html}
    </table></body></html>
    """


async def client_for(pages: dict[str, str], settings_: Settings | None = None):
    source = MemoryPageSource(pages)
    fetcher = Fetcher(settings_ or settings(), clock=FakeClock(), page_source=source)
    return source, Client(create_server(settings_ or settings(), fetcher=fetcher))


def table_headers(answer_markdown: str) -> list[str]:
    header = next(line for line in answer_markdown.splitlines() if line.startswith("| "))
    return [cell.strip() for cell in header.strip("|").split("|")]


@pytest.mark.parametrize(
    ("class_id", "expected"),
    [(1, 100), (2, 100), (3, 50), (6, 100), (11, 200)],
)
def test_unfiltered_bowling_rate_default_minimums(class_id: int, expected: int) -> None:
    assert _comparison_default_minimum(
        (bowling_metric("bowling_average"),), class_id, period=None, filters={}
    ) == ("wickets", expected)


@pytest.mark.parametrize(
    ("class_id", "expected"),
    [(1, 30), (2, 30), (3, 20), (6, 50), (11, 50)],
)
def test_filtered_bowling_rate_default_minimums(class_id: int, expected: int) -> None:
    assert _comparison_default_minimum(
        (bowling_metric("bowling_average"),), class_id, period=None, filters={"continent": 2}
    ) == ("wickets", expected)


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
        row(2003, "Level Player", "CCC", "2016-2026", 60, 1200, "38.94", 850, "141.17", 1, 8),
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
            url(proof_query): result_page(rows[:9], total=9),
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
    assert "Level Player" not in [row["player"] for row in cold.structured_content["beaters"]]
    assert "Average Only" not in [row["player"] for row in cold.structured_content["beaters"]]
    assert {row["player"] for row in cold.structured_content["level"]} == {
        "Tie Player",
        "Level Player",
    }
    assert [row["player"] for row in cold.structured_content["ties"]] == ["Tie Player"]
    level_details = {row["player"]: row["detail"] for row in cold.structured_content["level"]}
    assert level_details["Tie Player"] == "level on batting average and strike rate"
    assert level_details["Level Player"] == "level on batting average, better on strike rate"
    level_structured = {row["player"]: row for row in cold.structured_content["level"]}
    assert level_structured["Tie Player"]["better_on"] == []
    assert level_structured["Tie Player"]["level_on"] == ["average", "strike_rate"]
    assert level_structured["Level Player"]["better_on"] == ["strike_rate"]
    assert level_structured["Level Player"]["level_on"] == ["average"]
    assert "level with Babar Azam" in cold.structured_content["answer_markdown"]
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


async def test_bowling_better_than_player_lower_metric_uses_qualmax_and_reports_level() -> None:
    period = ResolvedPeriod(start="2004-12-17", end="2019-02-21")
    base_query = StatsguruQuery(
        **{
            "class": 1,
            "type": "bowling",
            "period": period,
            "continent": 2,
            "qualifications": (Qualification(field="wickets", minimum=30),),
            "orderby": "bowling_average",
            "size": 200,
        }
    )
    proof_query = base_query.model_copy(
        update={
            "qualifications": (
                Qualification(field="wickets", minimum=30),
                Qualification(field="bowling_average", maximum="25.0099"),
            )
        }
    )
    form_url, form = career_form(47492, 1, "17 Dec 2004", "21 Feb 2019")
    target_spec = PlayerPageSpec(
        player_id=47492,
        **{
            "class": 1,
            "type": "bowling",
            "period": period,
            "continent": 2,
        },
    )
    rows = [
        bowling_row(1, "Better Bowler", "AAA", "2005-2018", 130, "22.00"),
        bowling_row(47492, "Dale Steyn", "SA", "2004-2019", 92, "25.00"),
        bowling_row(2, "Tie Bowler", "BBB", "2006-2018", 120, "25.00"),
        bowling_row(3, "Worse Bowler", "CCC", "2006-2018", 120, "26.00"),
    ]
    source, client = await client_for(
        {
            player_search_url("Dale Steyn"): search_page(
                search_row("Dale Steyn", "SA", 47492, 1, "Test matches", "2004/05 - 2018/19")
            ),
            form_url: form,
            target_spec.url(as_of=FakeClock().now().date()): bowling_player_page(
                '<tr class="data1"><td>unfiltered</td><td>2004-2019</td><td>93</td><td>171</td><td>3101.2</td><td>660</td><td>10077</td><td>439</td><td>7/51</td><td>22.95</td><td>3.24</td><td>42.3</td><td>29</td><td>5</td><td>0</td></tr>'
                '<tr class="data1"><td>filtered</td><td>2007-2018</td><td>22</td><td>42</td><td>657.4</td><td>140</td><td>2218</td><td>92</td><td>5/56</td><td>25.00</td><td>3.36</td><td>42.9</td><td>5</td><td>2</td><td>0</td></tr>'
            ),
            url(base_query): bowling_result_page(rows, total=4),
            url(proof_query): bowling_result_page(rows[:3], total=3),
        }
    )

    async with client:
        result = await client.call_tool(
            "better_than_player",
            {
                "player_name": "Dale Steyn",
                "format": "Test",
                "discipline": "bowling",
                "metrics": ["bowling_average"],
                "continent": "Asia",
            },
        )

    assert len(source.requests) == 5
    assert [row["player"] for row in result.structured_content["beaters"]] == ["Better Bowler"]
    assert [row["player"] for row in result.structured_content["ties"]] == ["Tie Bowler"]
    proof_url = result.structured_content["proof"]["url"]
    assert "qualval2=bowling_average" in proof_url
    assert "qualmax2=25.0099" in proof_url
    assert "qualmin2" not in proof_url
    assert result.structured_content["proof"]["confirmed"] is True
    assert "Minimum: wickets >= 30." in result.structured_content["answer_markdown"]
    assert "Lower bowling average is better." in result.structured_content["answer_markdown"]


async def test_bowling_better_than_player_handles_economy_strike_and_mixed_directions() -> None:
    period = ResolvedPeriod(start="2004-12-17", end="2019-02-21")
    base_query = StatsguruQuery(
        **{
            "class": 2,
            "type": "bowling",
            "period": period,
            "host": (6, 7),
            "qualifications": (Qualification(field="wickets", minimum=30),),
            "orderby": "economy_rate",
            "size": 200,
        }
    )
    proof_query = base_query.model_copy(
        update={
            "qualifications": (
                Qualification(field="wickets", minimum=30),
                Qualification(field="economy_rate", maximum="4.5099"),
                Qualification(field="bowling_strike_rate", maximum="35.0999"),
            )
        }
    )
    form_url, form = career_form(47492, 2, "17 Dec 2004", "21 Feb 2019")
    target_spec = PlayerPageSpec(
        player_id=47492,
        **{"class": 2, "type": "bowling", "period": period, "host": (6, 7)},
    )
    rows = [
        bowling_row(1, "Double Beater", "AAA", "2005-2018", 45, "22.00", econ="4.00", sr="30.0"),
        bowling_row(47492, "Dale Steyn", "SA", "2004-2019", 40, "25.00", econ="4.50", sr="35.0"),
        bowling_row(2, "Economy Only", "BBB", "2006-2018", 40, "26.00", econ="4.00", sr="40.0"),
        bowling_row(3, "Strike Only", "CCC", "2006-2018", 40, "27.00", econ="5.00", sr="30.0"),
    ]
    source, client = await client_for(
        {
            player_search_url("Dale Steyn"): search_page(
                search_row("Dale Steyn", "SA", 47492, 2, "One-Day Internationals", "2004 - 2019")
            ),
            form_url: form,
            target_spec.url(as_of=FakeClock().now().date()): bowling_player_page(
                '<tr class="data1"><td>filtered</td><td>2004-2019</td><td>20</td><td>20</td><td>200.0</td><td>10</td><td>900</td><td>40</td><td>5/50</td><td>25.00</td><td>4.50</td><td>35.0</td><td>1</td><td>1</td><td>0</td></tr>'
            ),
            url(base_query): bowling_result_page(rows, total=4),
            url(proof_query): bowling_result_page(rows[:2], total=2),
        }
    )
    mixed_query = base_query.model_copy(update={"orderby": "five_wickets"})
    mixed_proof = mixed_query.model_copy(
        update={
            "qualifications": (
                Qualification(field="wickets", minimum=30),
                Qualification(field="five_wickets", minimum=5),
                Qualification(field="bowling_average", maximum="25.0099"),
            )
        }
    )
    source.pages[url(mixed_query)] = bowling_result_page(rows, total=4)
    source.pages[url(mixed_proof)] = bowling_result_page(rows[:2], total=2)

    async with client:
        result = await client.call_tool(
            "better_than_player",
            {
                "player_name": "Dale Steyn",
                "format": "ODI",
                "discipline": "bowling",
                "metrics": ["economy", "strike rate"],
                "host_country": ["India", "Pakistan"],
            },
        )
        mixed = await client.call_tool(
            "better_than_player",
            {
                "player_name": "Dale Steyn",
                "format": "ODI",
                "discipline": "bowling",
                "metrics": ["five-fors", "average"],
                "host_country": ["India", "Pakistan"],
            },
        )

    assert [row["player"] for row in result.structured_content["beaters"]] == ["Double Beater"]
    assert result.structured_content["proof"]["confirmed"] is True
    assert (
        "Lower economy rate and bowling strike rate are better."
        in result.structured_content["answer_markdown"]
    )

    assert mixed.structured_content["proof"]["confirmed"] is True
    assert "Higher five-wicket hauls is better" in mixed.structured_content["answer_markdown"]
    assert "Lower bowling average is better" in mixed.structured_content["answer_markdown"]


async def test_bowling_comparison_floor_never_exceeds_target_filtered_wickets() -> None:
    period = ResolvedPeriod(start="2020-01-01", end="2026-01-01")
    base_query = StatsguruQuery(
        **{
            "class": 3,
            "type": "bowling",
            "period": period,
            "continent": 2,
            "qualifications": (Qualification(field="wickets", minimum=15),),
            "orderby": "bowling_strike_rate",
            "size": 200,
        }
    )
    proof_query = base_query.model_copy(
        update={
            "qualifications": (
                Qualification(field="wickets", minimum=15),
                Qualification(field="bowling_strike_rate", maximum="18.0999"),
            )
        }
    )
    form_url, form = career_form(999, 3, "01 Jan 2020", "01 Jan 2026")
    target_spec = PlayerPageSpec(
        player_id=999,
        **{"class": 3, "type": "bowling", "period": period, "continent": 2},
    )
    rows = [
        bowling_row(1, "Fast Wickets", "AAA", "2020-2026", 20, "20.00", sr="15.0"),
        bowling_row(999, "Target Bowler", "BBB", "2020-2026", 15, "25.00", sr="18.0"),
    ]
    source, client = await client_for(
        {
            player_search_url("Target Bowler"): search_page(
                search_row("Target Bowler", "BBB", 999, 3, "Twenty20 Internationals", "2020 - 2026")
            ),
            form_url: form,
            target_spec.url(as_of=FakeClock().now().date()): bowling_player_page(
                '<tr class="data1"><td>unfiltered</td><td>2020-2026</td><td>20</td><td>20</td><td>100.0</td><td>3</td><td>600</td><td>40</td><td>4/20</td><td>15.00</td><td>6.00</td><td>15.0</td><td>2</td><td>0</td><td>0</td></tr>'
                '<tr class="data1"><td>filtered</td><td>2020-2026</td><td>10</td><td>10</td><td>45.0</td><td>1</td><td>270</td><td>15</td><td>4/20</td><td>18.00</td><td>6.00</td><td>18.0</td><td>1</td><td>0</td><td>0</td></tr>'
            ),
            url(base_query): bowling_result_page(rows, total=2),
            url(proof_query): bowling_result_page(rows, total=2),
        }
    )

    async with client:
        result = await client.call_tool(
            "better_than_player",
            {
                "player_name": "Target Bowler",
                "format": "T20I",
                "discipline": "bowling",
                "metrics": ["strike rate"],
                "continent": "Asia",
            },
        )

    assert result.structured_content["proof"]["confirmed"] is True
    assert "Minimum: wickets >= 15." in result.structured_content["answer_markdown"]


async def test_bowling_count_comparison_floor_caps_to_target_filtered_row() -> None:
    period = ResolvedPeriod(start="2004-12-17", end="2019-02-21")
    base_query = StatsguruQuery(
        **{
            "class": 1,
            "type": "bowling",
            "period": period,
            "continent": 2,
            "qualifications": (Qualification(field="wickets", minimum=92),),
            "orderby": "wickets",
            "size": 200,
        }
    )
    proof_query = base_query.model_copy(
        update={
            "qualifications": (
                Qualification(field="wickets", minimum=92),
                Qualification(field="wickets", minimum=92),
            )
        }
    )
    form_url, form = career_form(47492, 1, "17 Dec 2004", "21 Feb 2019")
    target_spec = PlayerPageSpec(
        player_id=47492, **{"class": 1, "type": "bowling", "period": period, "continent": 2}
    )
    rows = [
        bowling_row(47492, "Dale Steyn", "SA", "2004-2019", 92, "24.11"),
        bowling_row(1, "More Wickets", "AAA", "2004-2019", 100, "30.00"),
    ]
    source, client = await client_for(
        {
            player_search_url("Dale Steyn"): search_page(
                search_row("Dale Steyn", "SA", 47492, 1, "Test matches", "2004/05 - 2018/19")
            ),
            form_url: form,
            target_spec.url(as_of=FakeClock().now().date()): bowling_player_page(
                '<tr class="data1"><td>unfiltered</td><td>2004-2019</td><td>93</td><td>171</td><td>3101.2</td><td>660</td><td>10077</td><td>439</td><td>7/51</td><td>22.95</td><td>3.24</td><td>42.3</td><td>26</td><td>5</td><td>0</td></tr>'
                '<tr class="data1"><td>filtered</td><td>2006-2018</td><td>22</td><td>38</td><td>659.1</td><td>120</td><td>2219</td><td>92</td><td>7/51</td><td>24.11</td><td>3.36</td><td>42.9</td><td>5</td><td>1</td><td>0</td></tr>'
            ),
            url(base_query): bowling_result_page(rows, total=2),
            url(proof_query): bowling_result_page(rows, total=2),
        }
    )

    async with client:
        result = await client.call_tool(
            "better_than_player",
            {
                "player_name": "Dale Steyn",
                "format": "Test",
                "discipline": "bowling",
                "metrics": ["wickets"],
                "continent": "Asia",
            },
        )

    assert result.structured_content["proof"]["confirmed"] is True
    assert "Minimum: wickets >= 92." in result.structured_content["answer_markdown"]
    assert [row["player"] for row in result.structured_content["beaters"]] == ["More Wickets"]


async def test_bowling_five_wickets_floor_caps_to_filtered_row() -> None:
    period = ResolvedPeriod(start="2003-05-22", end="2024-07-10")
    base_query = StatsguruQuery(
        **{
            "class": 1,
            "type": "bowling",
            "period": period,
            "continent": 2,
            "qualifications": (Qualification(field="five_wickets", minimum=2),),
            "orderby": "five_wickets",
            "size": 200,
        }
    )
    proof_query = base_query.model_copy(
        update={
            "qualifications": (
                Qualification(field="five_wickets", minimum=2),
                Qualification(field="five_wickets", minimum=2),
            )
        }
    )
    form_url, form = career_form(8608, 1, "22 May 2003", "10 Jul 2024")
    target_spec = PlayerPageSpec(
        player_id=8608, **{"class": 1, "type": "bowling", "period": period, "continent": 2}
    )
    rows = [
        bowling_row(8608, "JM Anderson", "ENG", "2003-2024", 92, "27.51", five=2),
        bowling_row(1, "Five-for Bowler", "AAA", "2004-2020", 80, "24.00", five=3),
    ]
    source, client = await client_for(
        {
            player_search_url("JM Anderson"): search_page(
                search_row("JM Anderson", "ENG", 8608, 1, "Test matches", "2003 - 2024")
            ),
            form_url: form,
            target_spec.url(as_of=FakeClock().now().date()): bowling_player_page(
                '<tr class="data1"><td>unfiltered</td><td>2003-2024</td><td>188</td><td>350</td><td>6672.5</td><td>1730</td><td>18627</td><td>704</td><td>7/42</td><td>26.45</td><td>2.79</td><td>56.8</td><td>32</td><td>3</td><td>0</td></tr>'
                '<tr class="data1"><td>filtered</td><td>2003-2024</td><td>32</td><td>58</td><td>976.2</td><td>251</td><td>2531</td><td>92</td><td>6/40</td><td>27.51</td><td>2.59</td><td>63.6</td><td>2</td><td>2</td><td>0</td></tr>'
            ),
            url(base_query): bowling_result_page(rows, total=2),
            url(proof_query): bowling_result_page(rows, total=2),
        }
    )

    async with client:
        result = await client.call_tool(
            "better_than_player",
            {
                "player_name": "JM Anderson",
                "format": "Test",
                "discipline": "bowling",
                "metrics": ["five-fors"],
                "continent": "Asia",
            },
        )

    assert result.structured_content["proof"]["confirmed"] is True
    assert "Minimum: five_wickets >= 2." in result.structured_content["answer_markdown"]
    assert [row["player"] for row in result.structured_content["beaters"]] == ["Five-for Bowler"]


async def test_bowling_default_career_span_counts_as_filtered_floor() -> None:
    period = ResolvedPeriod(start="2010-01-01", end="2020-01-01")
    base_query = StatsguruQuery(
        **{
            "class": 2,
            "type": "bowling",
            "period": period,
            "qualifications": (Qualification(field="wickets", minimum=30),),
            "orderby": "bowling_average",
            "size": 200,
        }
    )
    proof_query = base_query.model_copy(
        update={
            "qualifications": (
                Qualification(field="wickets", minimum=30),
                Qualification(field="bowling_average", maximum="30.0099"),
            )
        }
    )
    form_url, form = career_form(321, 2, "01 Jan 2010", "01 Jan 2020")
    target_spec = PlayerPageSpec(player_id=321, **{"class": 2, "type": "bowling", "period": period})
    rows = [
        bowling_row(1, "Average Beater", "AAA", "2010-2020", 35, "25.00"),
        bowling_row(321, "Career Target", "BBB", "2010-2020", 40, "30.00"),
    ]
    source, client = await client_for(
        {
            player_search_url("Career Target"): search_page(
                search_row("Career Target", "BBB", 321, 2, "One-Day Internationals", "2010 - 2020")
            ),
            form_url: form,
            target_spec.url(as_of=FakeClock().now().date()): bowling_player_page(
                '<tr class="data1"><td>filtered</td><td>2010-2020</td><td>30</td><td>30</td><td>300.0</td><td>15</td><td>1200</td><td>40</td><td>5/40</td><td>30.00</td><td>4.00</td><td>45.0</td><td>2</td><td>1</td><td>0</td></tr>'
            ),
            url(base_query): bowling_result_page(rows, total=2),
            url(proof_query): bowling_result_page(rows, total=2),
        }
    )

    async with client:
        result = await client.call_tool(
            "better_than_player",
            {
                "player_name": "Career Target",
                "format": "ODI",
                "discipline": "bowling",
                "metrics": ["average"],
            },
        )

    assert result.structured_content["proof"]["confirmed"] is True
    assert "Minimum: wickets >= 30." in result.structured_content["answer_markdown"]


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


async def test_player_record_bowling_in_asia_for_two_players() -> None:
    anderson_spec = PlayerPageSpec(
        player_id=8608, **{"class": 1, "type": "bowling", "continent": 2}
    )
    steyn_spec = PlayerPageSpec(player_id=47492, **{"class": 1, "type": "bowling", "continent": 2})
    source, client = await client_for(
        {
            player_search_url("James Anderson"): search_page(
                search_row("James Anderson", "ENG", 8608, 1, "Test matches", "2003 - 2024")
            ),
            player_search_url("Dale Steyn"): search_page(
                search_row("Dale Steyn", "SA", 47492, 1, "Test matches", "2004/05 - 2018/19")
            ),
            anderson_spec.url(as_of=FakeClock().now().date()): bowling_player_page(
                '<tr class="data1"><td>unfiltered</td><td>2003-2024</td><td>188</td><td>350</td><td>6672.5</td><td>1730</td><td>18627</td><td>704</td><td>7/42</td><td>26.45</td><td>2.79</td><td>56.8</td><td>32</td><td>3</td><td>0</td></tr>'
                '<tr class="data1"><td>filtered</td><td>2006-2024</td><td>32</td><td>60</td><td>1625.3</td><td>420</td><td>2531</td><td>92</td><td>5/72</td><td>27.51</td><td>2.59</td><td>63.6</td><td>4</td><td>1</td><td>0</td></tr>'
            ),
            steyn_spec.url(as_of=FakeClock().now().date()): bowling_player_page(
                '<tr class="data1"><td>unfiltered</td><td>2004-2019</td><td>93</td><td>171</td><td>3101.2</td><td>660</td><td>10077</td><td>439</td><td>7/51</td><td>22.95</td><td>3.24</td><td>42.3</td><td>29</td><td>5</td><td>0</td></tr>'
                '<tr class="data1"><td>filtered</td><td>2007-2018</td><td>22</td><td>42</td><td>657.4</td><td>140</td><td>2218</td><td>92</td><td>5/56</td><td>24.11</td><td>3.36</td><td>42.9</td><td>5</td><td>2</td><td>0</td></tr>'
            ),
        }
    )

    async with client:
        anderson = await client.call_tool(
            "player_record",
            {
                "player_name": "James Anderson",
                "format": "Test",
                "discipline": "bowling",
                "continent": "Asia",
            },
        )
        steyn = await client.call_tool(
            "player_record",
            {
                "player_name": "Dale Steyn",
                "format": "Test",
                "discipline": "bowling",
                "continent": "Asia",
            },
        )

    assert len(source.requests) == 4
    assert anderson.structured_content["row"]["Wkts"] == 92
    assert steyn.structured_content["row"]["Ave"] == "24.11"
    assert "continent=2" in anderson.structured_content["proof"]["url"]
    assert "type=bowling" in steyn.structured_content["proof"]["url"]
    assert "wickets 92" in anderson.structured_content["answer_markdown"].split("\n", 1)[0]


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


async def test_bowling_leaderboard_lower_metric_uses_continent_and_ties() -> None:
    query = StatsguruQuery(
        **{
            "class": 1,
            "type": "bowling",
            "continent": 2,
            "qualifications": (Qualification(field="wickets", minimum=30),),
            "orderby": "bowling_average",
            "size": 10,
        }
    )
    source, client = await client_for(
        {
            url(query): bowling_result_page(
                [
                    bowling_row(1, "Alpha Bowler", "AAA", "2000-2010", 120, "20.00"),
                    bowling_row(2, "Beta Bowler", "BBB", "2000-2010", 110, "20.00"),
                    bowling_row(3, "Gamma Bowler", "CCC", "2000-2010", 150, "21.00"),
                ],
                total=3,
            )
        }
    )

    async with client:
        result = await client.call_tool(
            "leaderboard",
            {
                "format": "Test",
                "discipline": "bowling",
                "metric": "average",
                "continent": "Asia",
                "top_n": 1,
            },
        )

    assert len(source.requests) == 1
    assert "continent=2" in source.requests[0]
    assert [row["rank"] for row in result.structured_content["rows"]] == [1, 1]
    assert [row["player"] for row in result.structured_content["rows"]] == [
        "Alpha Bowler",
        "Beta Bowler",
    ]
    assert result.structured_content["rows"][0]["value"] == "20.00"
    assert result.structured_content["rows"][0]["wickets"] == 120
    assert "Minimum: wickets >= 30." in result.structured_content["answer_markdown"]
    assert "Lower bowling average is better." in result.structured_content["answer_markdown"]


async def test_bowling_rate_leaderboard_follows_real_statsguru_order_across_pages() -> None:
    query = StatsguruQuery(
        **{
            "class": 1,
            "type": "bowling",
            "qualifications": (Qualification(field="wickets", minimum=100),),
            "orderby": "bowling_average",
            "size": 25,
        }
    )
    page2 = query.model_copy(update={"page": 2})
    source, client = await client_for(
        {
            url(query): bowling_result_page(
                [
                    bowling_row(index, f"P{index}", "AAA", "2000-2010", 100, "20.00")
                    for index in range(1, 26)
                ],
                pages=2,
                total=26,
            ),
            url(page2): bowling_result_page(
                [bowling_row(26, "P26", "AAA", "2000-2010", 100, "20.00")],
                page=2,
                pages=2,
                total=26,
            ),
        }
    )

    async with client:
        result = await client.call_tool(
            "leaderboard",
            {
                "format": "Test",
                "discipline": "bowling",
                "metric": "bowling_average",
                "top_n": 10,
            },
        )

    assert len(source.requests) == 2
    assert "orderbyad=reverse" not in source.requests[0]
    assert len(result.structured_content["rows"]) == 26


async def test_filtered_t20i_bowling_rate_leaderboard_uses_filtered_floor() -> None:
    query = StatsguruQuery(
        **{
            "class": 3,
            "type": "bowling",
            "host": (6, 7),
            "qualifications": (Qualification(field="wickets", minimum=20),),
            "orderby": "economy_rate",
            "size": 10,
        }
    )
    source, client = await client_for(
        {
            url(query): bowling_result_page(
                [
                    bowling_row(1, "Economical", "AAA", "2020-2026", 22, "18.00", econ="5.00"),
                    bowling_row(2, "Costly", "BBB", "2020-2026", 30, "20.00", econ="6.00"),
                ],
                total=2,
            )
        }
    )

    async with client:
        result = await client.call_tool(
            "leaderboard",
            {
                "format": "T20I",
                "discipline": "bowling",
                "metric": "economy",
                "host_country": ["India", "Pakistan"],
                "top_n": 1,
            },
        )

    assert len(source.requests) == 1
    assert "host=6;host=7" in source.requests[0]
    assert "qualmin1=20" in source.requests[0]
    assert result.structured_content["rows"][0]["player"] == "Economical"
    assert result.structured_content["rows"][0]["value"] == "5.00"


async def test_sortable_leaderboard_follows_boundary_tie_to_next_page() -> None:
    query = StatsguruQuery(
        **{
            "class": 2,
            "type": "batting",
            "qualifications": (Qualification(field="innings", minimum=20),),
            "orderby": "batting_average",
            "size": 25,
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
                    for index in range(1, 26)
                ],
                pages=2,
                total=26,
            ),
            url(page2): result_page(
                [row(26, "P26", "AAA", "2000-2010", 20, 1000, "50.00", 1000, "100.00", 1, 1)],
                page=2,
                pages=2,
                total=26,
            ),
        }
    )

    async with client:
        result = await client.call_tool(
            "leaderboard", {"format": "ODI", "metric": "average", "top_n": 10}
        )

    assert len(source.requests) == 2
    assert len(result.structured_content["rows"]) == 26


async def test_sortable_leaderboard_boundary_tie_overflow_errors() -> None:
    query = StatsguruQuery(
        **{
            "class": 2,
            "type": "batting",
            "qualifications": (Qualification(field="innings", minimum=20),),
            "orderby": "batting_average",
            "size": 25,
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
                    for index in range(1, 26)
                ],
                pages=2,
                total=26,
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
    default_query = query.model_copy(update={"size": 25})
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
            ),
            url(default_query): result_page(
                [
                    row(
                        index,
                        f"D{index}",
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
                    for index in range(1, 26)
                ],
                pages=48,
                total=1200,
            ),
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
        assert (
            "Fetched 1 sorted Statsguru result page" in result.structured_content["answer_markdown"]
        )
        source.requests.clear()
        default = await client.call_tool("leaderboard", {"format": "ODI", "metric": "average"})

    assert default.is_error is False
    assert len(source.requests) == 1
    assert len(default.structured_content["rows"]) == 10


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
    assert "| Value | Name" in result.content[0].text
    assert "Kind" in result.content[0].text
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


async def test_filtered_wickets_leaderboard_without_minimum_returns_top_rows_with_ties() -> None:
    query = StatsguruQuery(
        **{
            "class": 1,
            "type": "bowling",
            "team": (1, 3),
            "continent": 2,
            "qualifications": (Qualification(field="wickets", minimum=1),),
            "orderby": "wickets",
            "size": 10,
        }
    )
    source, client = await client_for(
        {
            url(query): bowling_result_page(
                [
                    bowling_row(501, "Swing Bowler", "ENG", "2004-2022", 88, "26.10"),
                    bowling_row(502, "Pace Bowler", "SA", "2005-2019", 88, "23.40"),
                    bowling_row(503, "Finger Spinner", "ENG", "2017-2024", 71, "31.20"),
                    bowling_row(504, "Off Spinner", "ENG", "2009-2013", 64, "25.90"),
                    bowling_row(505, "Slow Left-armer", "ENG", "1969-1982", 64, "26.60"),
                    bowling_row(506, "Wrist Spinner", "SA", "2016-2024", 55, "29.70"),
                    bowling_row(507, "Seam Allrounder", "SA", "1996-2008", 55, "23.10"),
                    bowling_row(508, "Utility Spinner", "ENG", "2014-2023", 49, "35.80"),
                    bowling_row(509, "Part-timer", "ENG", "1999-2007", 41, "34.90"),
                    bowling_row(510, "Debut Quick", "SA", "2012-2016", 41, "36.10"),
                ],
                pages=25,
                total=245,
            )
        }
    )

    async with client:
        result = await client.call_tool(
            "leaderboard",
            {
                "format": "Test",
                "discipline": "bowling",
                "metric": "wickets",
                "team": ["England", "South Africa"],
                "continent": "Asia",
                "top_n": 5,
            },
        )

    assert result.is_error is False
    assert source.requests == [url(query)]
    assert "qualmin1=1;qualval1=wickets" in source.requests[0]
    assert [
        (row["rank"], row["player"], row["value"]) for row in result.structured_content["rows"]
    ] == [
        (1, "Swing Bowler", "88"),
        (1, "Pace Bowler", "88"),
        (3, "Finger Spinner", "71"),
        (4, "Off Spinner", "64"),
        (4, "Slow Left-armer", "64"),
    ]
    answer = result.structured_content["answer_markdown"]
    assert answer.startswith("Swing Bowler, Pace Bowler are tied for the lead with 88 wickets.")
    assert "Minimum: wickets >= 1." in answer


async def test_runs_leaderboard_at_ground_without_minimum_returns_top_n() -> None:
    form_url = (
        "https://stats.cricinfo.com/ci/engine/stats/index.html?class=1;filter=advanced;type=batting"
    )
    query = StatsguruQuery(
        **{
            "class": 1,
            "type": "batting",
            "ground": 2001,
            "qualifications": (Qualification(field="runs", minimum=1),),
            "orderby": "runs",
            "size": 10,
        }
    )
    source, client = await client_for(
        {
            form_url: """
            <form name="gurumenu">
            <select name="ground"><option value="2001">QQQ: Example Oval</option></select>
            </form>
            """,
            url(query): result_page(
                [
                    row(601, "Leader", "AAA", "2001-2015", 14, 812, "62.46", 1400, "58.00", 3, 4),
                    row(602, "Second", "BBB", "2004-2019", 15, 790, "56.42", 1500, "52.66", 2, 5),
                    row(603, "Third", "AAA", "2010-2024", 12, 655, "54.58", 1200, "54.58", 2, 3),
                    row(604, "Fourth", "CCC", "1998-2006", 10, 540, "60.00", 1000, "54.00", 1, 3),
                ],
                pages=9,
                total=86,
            ),
        }
    )

    async with client:
        result = await client.call_tool(
            "leaderboard",
            {"format": "Test", "metric": "runs", "ground": "Example Oval", "top_n": 3},
        )

    assert result.is_error is False
    assert source.requests == [form_url, url(query)]
    assert "ground=2001" in source.requests[1]
    assert "qualmin1=1;qualval1=runs" in source.requests[1]
    assert [
        (row["rank"], row["player"], row["value"]) for row in result.structured_content["rows"]
    ] == [(1, "Leader", "812"), (2, "Second", "790"), (3, "Third", "655")]
    assert "Minimum: runs >= 1." in result.structured_content["answer_markdown"]


@pytest.mark.parametrize(
    ("discipline", "metric", "field", "label", "minimum"),
    [
        ("batting", "runs", "runs", "runs", None),
        ("batting", "hundreds", "hundreds", "hundreds", None),
        ("batting", "fifties", "fifty_plus", "fifties (50-99)", None),
        ("bowling", "wickets", "wickets", "wickets", None),
        ("bowling", "five_wickets", "five_wickets", "five-wicket hauls", None),
        ("bowling", "ten_wickets", "ten_wickets", "ten-wicket matches", None),
        ("bowling", "wickets", "wickets", "wickets", 30),
    ],
)
async def test_count_leaderboards_show_the_ranked_column_once(
    discipline: str, metric: str, field: str, label: str, minimum: int | None
) -> None:
    query = StatsguruQuery(
        **{
            "class": 1,
            "type": discipline,
            "qualifications": (Qualification(field=field, minimum=minimum or 1),),
            "orderby": field,
            "size": 10,
        }
    )
    page = (
        result_page(
            [
                row(1, "First", "AAA", "2000-2010", 40, 900, "45.00", 1500, "60.00", 3, 5),
                row(2, "Second", "BBB", "2000-2010", 35, 800, "40.00", 1400, "57.14", 2, 4),
                row(3, "Third", "CCC", "2000-2010", 30, 700, "35.00", 1300, "53.84", 1, 3),
            ]
        )
        if discipline == "batting"
        else bowling_result_page(
            [
                bowling_row(1, "First", "AAA", "2000-2010", 90, "25.00", five=4, ten=3),
                bowling_row(2, "Second", "BBB", "2000-2010", 80, "26.00", five=3, ten=2),
                bowling_row(3, "Third", "CCC", "2000-2010", 70, "27.00", five=2, ten=1),
            ]
        )
    )
    source, client = await client_for({url(query): page})
    arguments: dict[str, object] = {
        "format": "Test",
        "discipline": discipline,
        "metric": metric,
        "top_n": 2,
    }
    if minimum is not None:
        arguments["minimum"] = minimum

    async with client:
        result = await client.call_tool("leaderboard", arguments)

    assert source.requests == [url(query)]
    answer = result.structured_content["answer_markdown"]
    assert table_headers(answer) == ["Rank", "Player", label]
    assert f"Minimum: {field} >= {minimum or 1}." in answer
    assert [row["player"] for row in result.structured_content["rows"]] == ["First", "Second"]


@pytest.mark.parametrize(
    ("arguments", "query_fields", "headers", "floor_text"),
    [
        (
            {"format": "Test", "metric": "average", "top_n": 2},
            {"type": "batting", "orderby": "batting_average", "size": 10},
            ["Rank", "Player", "batting average", "innings"],
            "Minimum: innings >= 20.",
        ),
        (
            {
                "format": "Test",
                "discipline": "bowling",
                "metric": "average",
                "continent": "Asia",
                "top_n": 2,
            },
            {"type": "bowling", "continent": 2, "orderby": "bowling_average", "size": 10},
            ["Rank", "Player", "bowling average", "wickets"],
            "Minimum: wickets >= 30.",
        ),
        (
            {"format": "ODI", "metric": "innings_per_hundred", "top_n": 2},
            {"type": "batting", "orderby": "hundreds", "orderbyad": "reverse", "size": 200},
            ["Rank", "Player", "innings per hundred", "hundreds"],
            "Minimum: hundreds >= 5.",
        ),
    ],
)
async def test_rate_and_derived_leaderboards_keep_default_floors(
    arguments: dict[str, object],
    query_fields: dict[str, object],
    headers: list[str],
    floor_text: str,
) -> None:
    field, minimum = floor_text.removeprefix("Minimum: ").removesuffix(".").split(" >= ")
    query = StatsguruQuery(
        **{
            "class": 1 if arguments["format"] == "Test" else 2,
            "qualifications": (Qualification(field=field, minimum=int(minimum)),),
            **query_fields,
        }
    )
    page = (
        result_page(
            [
                row(1, "First", "AAA", "2000-2010", 40, 2400, "60.00", 3000, "80.00", 8, 5),
                row(2, "Second", "BBB", "2000-2010", 50, 2500, "50.00", 3100, "80.64", 6, 9),
                row(3, "Third", "CCC", "2000-2010", 60, 2600, "43.33", 3200, "81.25", 5, 12),
            ]
        )
        if query_fields["type"] == "batting"
        else bowling_result_page(
            [
                bowling_row(1, "First", "AAA", "2000-2010", 45, "21.00"),
                bowling_row(2, "Second", "BBB", "2000-2010", 60, "22.00"),
                bowling_row(3, "Third", "CCC", "2000-2010", 75, "23.00"),
            ]
        )
    )
    source, client = await client_for({url(query): page})

    async with client:
        result = await client.call_tool("leaderboard", arguments)

    assert source.requests == [url(query)]
    assert f"qualmin1={minimum};qualval1={field}" in source.requests[0]
    answer = result.structured_content["answer_markdown"]
    assert table_headers(answer) == headers
    assert floor_text in answer
    assert [row["player"] for row in result.structured_content["rows"]] == ["First", "Second"]


async def test_batting_comparison_lowers_floor_when_target_is_below_default_under_filter() -> None:
    period = ResolvedPeriod(start="2016-01-01", end="2024-01-01")
    default_query = StatsguruQuery(
        **{
            "class": 3,
            "type": "batting",
            "period": period,
            "host": 27,
            "qualifications": (Qualification(field="runs", minimum=1000),),
            "orderby": "batting_average",
            "size": 200,
        }
    )
    lowered_query = default_query.model_copy(
        update={"qualifications": (Qualification(field="runs", minimum=506),)}
    )
    proof_query = default_query.model_copy(
        update={
            "qualifications": (
                Qualification(field="runs", minimum=506),
                Qualification(field="batting_average", minimum=Decimal("42.16")),
            )
        }
    )
    form_url, form = career_form(777, 3, "01 Jan 2016", "01 Jan 2024")
    target_spec = PlayerPageSpec(
        player_id=777, **{"class": 3, "type": "batting", "period": period, "host": 27}
    )
    target_page_url = target_spec.url(as_of=FakeClock().now().date())
    home_rows = [
        row(804, "Home Opener", "UAE", "2016-2023", 52, 1450, "31.20", 1200, "120.83", 1, 8),
        row(805, "Home Captain", "UAE", "2016-2023", 48, 1210, "28.13", 1010, "119.80", 0, 7),
    ]
    beat_rows = [
        row(801, "Visiting Star", "VVV", "2018-2022", 12, 610, "61.00", 450, "135.55", 1, 5),
        row(777, "Target Batter", "TTT", "2016-2023", 15, 506, "42.16", 400, "126.50", 0, 3),
        row(803, "Level Batter", "LLL", "2019-2023", 14, 520, "42.16", 420, "123.80", 0, 4),
    ]
    source, client = await client_for(
        {
            player_search_url("Target Batter"): search_page(
                search_row("Target Batter", "TTT", 777, 3, "Twenty20 Internationals", "2016 - 2024")
            ),
            form_url: form,
            url(default_query): result_page(home_rows, total=2),
            target_page_url: player_page(
                '<tr class="data1"><td>unfiltered</td><td>2016-2024</td><td>60</td><td>58</td><td>8</td><td>1650</td><td>91</td><td>33.00</td><td>1300</td><td>126.92</td><td>0</td><td>10</td><td>3</td></tr>'
                '<tr class="data1"><td>filtered</td><td>2016-2023</td><td>16</td><td>15</td><td>3</td><td>506</td><td>77*</td><td>42.16</td><td>400</td><td>126.50</td><td>0</td><td>3</td><td>0</td></tr>'
            ),
            url(lowered_query): result_page([*beat_rows, *home_rows], total=5),
            url(proof_query): result_page(beat_rows, total=3),
        }
    )
    arguments = {
        "player_name": "Target Batter",
        "format": "T20I",
        "metrics": ["average"],
        "host_country": "United Arab Emirates",
    }

    async with client:
        result = await client.call_tool("better_than_player", arguments)
        assert source.requests == [
            player_search_url("Target Batter"),
            form_url,
            url(default_query),
            target_page_url,
            url(lowered_query),
            url(proof_query),
        ]
        source.requests.clear()
        explicit = await client.call_tool("better_than_player", {**arguments, "minimum": 1000})

    assert result.is_error is False
    rows = result.structured_content["rows"]
    assert [(row["player"], row["relation"]) for row in rows] == [
        ("Visiting Star", "beats"),
        ("Target Batter", "target"),
        ("Level Batter", "level"),
    ]
    assert result.structured_content["proof"]["confirmed"] is True
    answer = result.structured_content["answer_markdown"]
    assert "Minimum: runs >= 506." in answer
    assert (
        "Lowered from the default runs >= 1000 to Target Batter's own figure, "
        "so Target Batter qualifies." in answer
    )
    assert explicit.is_error is True
    assert "Target Batter was not in the qualifying Statsguru rows" in explicit.content[0].text
    assert source.requests == []


async def test_batting_comparison_refetch_counts_against_the_page_limit() -> None:
    period = ResolvedPeriod(start="2016-01-01", end="2024-01-01")
    default_query = StatsguruQuery(
        **{
            "class": 3,
            "type": "batting",
            "period": period,
            "host": 27,
            "qualifications": (Qualification(field="runs", minimum=1000),),
            "orderby": "batting_average",
            "size": 200,
        }
    )
    lowered_query = default_query.model_copy(
        update={"qualifications": (Qualification(field="runs", minimum=506),)}
    )
    form_url, form = career_form(777, 3, "01 Jan 2016", "01 Jan 2024")
    target_spec = PlayerPageSpec(
        player_id=777, **{"class": 3, "type": "batting", "period": period, "host": 27}
    )
    home = row(804, "Home Opener", "UAE", "2016-2023", 52, 1450, "31.20", 1200, "120.83", 1, 8)
    target = row(777, "Target Batter", "TTT", "2016-2023", 15, 506, "42.16", 400, "126.50", 0, 3)
    source, client = await client_for(
        {
            player_search_url("Target Batter"): search_page(
                search_row("Target Batter", "TTT", 777, 3, "Twenty20 Internationals", "2016 - 2024")
            ),
            form_url: form,
            url(default_query): result_page([home]),
            target_spec.url(as_of=FakeClock().now().date()): player_page(
                '<tr class="data1"><td>filtered</td><td>2016-2023</td><td>16</td><td>15</td><td>3</td><td>506</td><td>77*</td><td>42.16</td><td>400</td><td>126.50</td><td>0</td><td>3</td><td>0</td></tr>'
            ),
            url(lowered_query): result_page([target], pages=2, total=201),
        },
        settings(max_pages=2),
    )

    async with client:
        result = await client.call_tool(
            "better_than_player",
            {
                "player_name": "Target Batter",
                "format": "T20I",
                "metrics": ["average"],
                "host_country": "United Arab Emirates",
            },
        )

    assert result.is_error is True
    assert "it needs 2 pages, but this call has 1 of its 2 pages left" in result.content[0].text
    assert url(lowered_query.model_copy(update={"page": 2})) not in source.requests


async def test_answer_metric_schema_lists_batting_and_bowling_keys() -> None:
    _source, client = await client_for({})

    async with client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    def metric_enum(schema: dict, prop: dict) -> list[str]:
        ref = next(option["$ref"] for option in prop["anyOf"] if "$ref" in option)
        return schema["$defs"][ref.rsplit("/", 1)[-1]]["enum"]

    leaderboard = tools["leaderboard"].input_schema
    comparison = tools["better_than_player"].input_schema
    expected = [*BATTING_METRICS, *BOWLING_METRICS]
    assert metric_enum(leaderboard, leaderboard["properties"]["metric"]) == expected
    assert metric_enum(comparison, comparison["properties"]["metrics"]["items"]) == expected
    assert {
        "wickets",
        "bowling_average",
        "economy_rate",
        "bowling_strike_rate",
        "five_wickets",
        "ten_wickets",
    } <= set(expected)
