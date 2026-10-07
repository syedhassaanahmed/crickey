from __future__ import annotations

# ruff: noqa: E501
import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from mcp import Client

from crickey.fetcher import Fetcher, FetchResponse, MemoryPageSource
from crickey.metrics import BATTING_METRICS, BOWLING_METRICS, batting_metric, bowling_metric
from crickey.query import PlayerPageSpec, Qualification, ResolvedPeriod, StatsguruQuery
from crickey.resolve import names_agree
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
    name: str,
    country: str,
    player_id: int,
    class_id: int,
    label: str,
    span: str,
    matches: int = 145,
) -> str:
    return f'<tr><td>{name}</td><td>{country}</td><td><a href="/ci/engine/player/{player_id}.html?class={class_id};type=allround">{label} player</a> ({span}, {matches} matches)</td></tr>'


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


def breadcrumb(player_id: int, name: str, format_label: str) -> str:
    return f"""
    <div class="icc-home"><a href="/ci/engine/player/{player_id}.html">
    Statistics / Statsguru / {name} / {format_label}
    </a></div>
    """


def named(page: str, player_id: int, name: str, format_label: str) -> str:
    return page.replace("<body>", "<body>" + breadcrumb(player_id, name, format_label), 1)


def no_records_player_page(player_id: int, name: str, format_label: str) -> str:
    return f"""
    <html><body>{breadcrumb(player_id, name, format_label)}
    <table class="engineTable"><caption>Career summary</caption>
    <tr class="data1"><td class="left"><b>No records available to match this query</b></td></tr>
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
    proof = cold.structured_content["proof"]
    assert ";orderby=hundreds;" in proof["url"]
    assert "orderbyad" not in proof["url"]
    assert "reverse" not in proof["label"]
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
    pages = {
        player_search_url("Babar Azam"): search_page(
            search_row("Babar Azam", "PAK", 348144, 3, "Twenty20 Internationals", "2016 - 2026")
        ),
        form_url: form,
        url(base_query): result_page(rows, total=182),
        url(proof_query): result_page(rows[:9], total=9),
    }
    source, client = await client_for(pages)

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

    # Golden question 3 by ID: the same career span, comparison and proof, with no search.
    by_id_source, by_id_client = await client_for(pages)
    async with by_id_client:
        by_id = await by_id_client.call_tool(
            "better_than_player",
            {"player_id": 348144, "format": "T20I", "metrics": ["average", "strike_rate"]},
        )

    assert by_id_source.requests == [form_url, url(base_query), url(proof_query)]
    assert by_id.structured_content["beaters"] == cold.structured_content["beaters"]
    assert by_id.structured_content["proof"] == cold.structured_content["proof"]
    period_line = "\n- Period: 2016-09-07 to 2026-02-24.\n"
    assert period_line in cold.structured_content["answer_markdown"]
    assert period_line in by_id.structured_content["answer_markdown"]
    assert by_id.structured_content["answer_markdown"] == cold.structured_content["answer_markdown"]


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
    assert (
        "Lowered from the default wickets >= 100 to Dale Steyn's own figure, "
        "so Dale Steyn qualifies." in result.structured_content["answer_markdown"]
    )
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
    ("arguments", "query_fields", "headers", "floor"),
    [
        (
            {"format": "Test", "metric": "average"},
            {"class": 1, "type": "batting", "orderby": "batting_average", "size": 10},
            ["Rank", "Player", "batting average", "innings"],
            ("innings", 20),
        ),
        (
            {"format": "ODI", "metric": "strike_rate"},
            {"class": 2, "type": "batting", "orderby": "batting_strike_rate", "size": 10},
            ["Rank", "Player", "strike rate", "balls_faced"],
            ("balls_faced", 500),
        ),
        (
            {"format": "ODI", "metric": "innings_per_hundred"},
            {"class": 2, "type": "batting", "orderby": "hundreds"},
            ["Rank", "Player", "innings per hundred", "hundreds"],
            ("hundreds", 5),
        ),
        (
            {"format": "Test", "metric": "innings_per_fifty_plus"},
            {"class": 1, "type": "batting", "orderby": "hundreds"},
            ["Rank", "Player", "innings per fifty-plus score", "hundreds"],
            ("hundreds", 5),
        ),
        (
            {"format": "ODI", "metric": "balls_per_dismissal"},
            {"class": 2, "type": "batting", "orderby": "hundreds"},
            ["Rank", "Player", "balls per dismissal", "hundreds"],
            ("hundreds", 5),
        ),
        (
            {"format": "Test", "discipline": "bowling", "metric": "average", "continent": "Asia"},
            {
                "class": 1,
                "type": "bowling",
                "continent": 2,
                "orderby": "bowling_average",
                "size": 10,
            },
            ["Rank", "Player", "bowling average", "wickets"],
            ("wickets", 30),
        ),
        (
            {"format": "ODI", "discipline": "bowling", "metric": "economy_rate"},
            {"class": 2, "type": "bowling", "orderby": "economy_rate", "size": 10},
            ["Rank", "Player", "economy rate", "wickets"],
            ("wickets", 100),
        ),
        (
            {
                "format": "T20I",
                "discipline": "bowling",
                "metric": "bowling_strike_rate",
                "continent": "Asia",
            },
            {
                "class": 3,
                "type": "bowling",
                "continent": 2,
                "orderby": "bowling_strike_rate",
                "size": 10,
            },
            ["Rank", "Player", "bowling strike rate", "wickets"],
            ("wickets", 20),
        ),
    ],
)
async def test_rate_and_derived_leaderboards_keep_default_floors(
    arguments: dict[str, object],
    query_fields: dict[str, object],
    headers: list[str],
    floor: tuple[str, int],
) -> None:
    field, minimum = floor
    query = StatsguruQuery(
        **{
            "size": 200,
            **query_fields,
            "qualifications": (Qualification(field=field, minimum=minimum),),
        }
    )
    page = (
        result_page(
            [
                row(1, "First", "AAA", "2000-2010", 40, 2400, "60.00", 2000, "120.00", 8, 10),
                row(2, "Second", "BBB", "2000-2010", 50, 2500, "50.00", 2400, "104.16", 6, 9),
                row(3, "Third", "CCC", "2000-2010", 60, 2600, "43.33", 2700, "96.29", 5, 12),
            ]
        )
        if query_fields["type"] == "batting"
        else bowling_result_page(
            [
                bowling_row(1, "First", "AAA", "2000-2010", 45, "21.00", econ="2.50", sr="45.0"),
                bowling_row(2, "Second", "BBB", "2000-2010", 60, "22.00", econ="2.60", sr="50.0"),
                bowling_row(3, "Third", "CCC", "2000-2010", 75, "23.00", econ="2.70", sr="55.0"),
            ]
        )
    )
    source, client = await client_for({url(query): page})

    async with client:
        result = await client.call_tool("leaderboard", {**arguments, "top_n": 2})

    assert source.requests == [url(query)]
    assert f"qualmin1={minimum};qualval1={field}" in source.requests[0]
    answer = result.structured_content["answer_markdown"]
    assert table_headers(answer) == headers
    assert f"Minimum: {field} >= {minimum}." in answer
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


def team_filter_case(discipline: str, all_teams_figure: int):
    batting = discipline == "batting"
    filters = {"host": 27} if batting else {"continent": 2}
    base = {
        "class": 3 if batting else 1,
        "type": discipline,
        "team": 6 if batting else 1,
        "orderby": "batting_average" if batting else "bowling_average",
        "size": 200,
        **filters,
    }
    field = "runs" if batting else "wickets"

    def query(floor: int, *thresholds: Qualification) -> StatsguruQuery:
        return StatsguruQuery(
            **{**base, "qualifications": (Qualification(field=field, minimum=floor), *thresholds)}
        )

    target_url = PlayerPageSpec(
        player_id=902, **{"class": base["class"], "type": discipline, **filters}
    ).url(as_of=FakeClock().now().date())
    target_page = (
        player_page(
            f'<tr class="data1"><td>filtered</td><td>2016-2024</td><td>10</td><td>8</td><td>3</td><td>{all_teams_figure}</td><td>122*</td><td>68.75</td><td>255</td><td>134.90</td><td>1</td><td>3</td><td>1</td></tr>'
        )
        if batting
        else bowling_player_page(
            f'<tr class="data1"><td>filtered</td><td>2012-2020</td><td>9</td><td>17</td><td>290.0</td><td>40</td><td>750</td><td>{all_teams_figure}</td><td>5/60</td><td>30.00</td><td>2.58</td><td>69.6</td><td>1</td><td>1</td><td>0</td></tr>'
        )
    )
    search = search_page(
        search_row(
            "Team Target",
            "TTT",
            902,
            base["class"],
            "Twenty20 Internationals" if batting else "Test matches",
            "2010 - 2024",
        )
    )
    arguments = {
        "player_name": "Team Target",
        "format": "T20I" if batting else "Test",
        "discipline": discipline,
        "metrics": ["average"],
        "team": "India" if batting else "England",
        "period": {"kind": "all_time"},
        **({"host_country": "United Arab Emirates"} if batting else {"continent": "Asia"}),
    }
    return arguments, query, {player_search_url("Team Target"): search}, (target_url, target_page)


@pytest.mark.parametrize("discipline", ["batting", "bowling"])
async def test_comparison_under_team_filter_uses_a_one_team_players_figure(
    discipline: str,
) -> None:
    batting = discipline == "batting"
    figure = 344 if batting else 25
    arguments, query, pages, (target_url, target_page) = team_filter_case(discipline, figure)
    if batting:
        beater = row(903, "Better Batter", "IND", "2016-2024", 9, 400, "70.00", 300, "133.33", 1, 3)
        target = row(902, "Team Target", "IND", "2016-2024", 8, 344, "68.75", 255, "134.90", 1, 3)
        other = row(904, "Team Opener", "IND", "2016-2024", 30, 1100, "36.66", 846, "130.02", 1, 8)
        threshold = Qualification(field="batting_average", minimum=Decimal("68.75"))
        pages[url(query(1000))] = result_page([other])
        pages[target_url] = target_page
        pages[url(query(figure))] = result_page([beater, target, other])
        pages[url(query(figure, threshold))] = result_page([beater, target])
    else:
        beater = bowling_row(903, "Better Bowler", "ENG", "2010-2020", 40, "25.00")
        target = bowling_row(902, "Team Target", "ENG", "2012-2020", 25, "30.00")
        other = bowling_row(904, "Costly Bowler", "ENG", "2010-2020", 35, "33.00")
        threshold = Qualification(field="bowling_average", maximum=Decimal("30.0099"))
        pages[target_url] = target_page
        pages[url(query(figure))] = bowling_result_page([beater, target, other])
        pages[url(query(figure, threshold))] = bowling_result_page([beater, target])
    source, client = await client_for(pages)

    async with client:
        result = await client.call_tool("better_than_player", arguments)

    assert result.is_error is False
    assert source.requests == list(pages)
    assert [row["player"] for row in result.structured_content["beaters"]] == [
        "Better Batter" if batting else "Better Bowler"
    ]
    assert result.structured_content["proof"]["confirmed"] is True
    assert (
        f"Minimum: {'runs' if batting else 'wickets'} >= {figure}."
        in result.structured_content["answer_markdown"]
    )


@pytest.mark.parametrize("discipline", ["batting", "bowling"])
async def test_comparison_under_team_filter_asks_for_minimum_when_still_missing(
    discipline: str,
) -> None:
    batting = discipline == "batting"
    figure = 600 if batting else 25
    arguments, query, pages, (target_url, target_page) = team_filter_case(discipline, figure)
    if batting:
        other = row(904, "Team Opener", "IND", "2016-2024", 30, 1100, "36.66", 846, "130.02", 1, 8)
        pages[url(query(1000))] = result_page([other])
        pages[target_url] = target_page
        pages[url(query(figure))] = result_page([other])
    else:
        other = bowling_row(904, "Costly Bowler", "ENG", "2010-2020", 35, "33.00")
        pages[target_url] = target_page
        pages[url(query(figure))] = bowling_result_page([other])
    source, client = await client_for(pages)

    async with client:
        result = await client.call_tool("better_than_player", arguments)
        explicit = await client.call_tool("better_than_player", {**arguments, "minimum": figure})

    field = "runs" if batting else "wickets"
    assert result.is_error is True
    assert source.requests == list(pages)
    assert (
        f"Team Target was not in the qualifying Statsguru rows ({field} >= {figure}). "
        f"Team Target's {field} for that team can't be read from the player page, which has "
        "no team filter; pass minimum to set a lower one."
    ) in result.content[0].text
    assert explicit.is_error is True
    assert "Team Target was not in the qualifying Statsguru rows." in explicit.content[0].text
    assert "team filter" not in explicit.content[0].text


async def test_comparison_without_team_filter_reports_a_missing_target_plainly() -> None:
    query = StatsguruQuery(
        **{
            "class": 1,
            "type": "bowling",
            "continent": 2,
            "qualifications": (Qualification(field="wickets", minimum=30),),
            "orderby": "bowling_average",
            "size": 200,
        }
    )
    target_url = PlayerPageSpec(
        player_id=902, **{"class": 1, "type": "bowling", "continent": 2}
    ).url(as_of=FakeClock().now().date())
    pages = {
        player_search_url("Team Target"): search_page(
            search_row("Team Target", "TTT", 902, 1, "Test matches", "2010 - 2024")
        ),
        target_url: bowling_player_page(
            '<tr class="data1"><td>unfiltered</td><td>2012-2020</td><td>9</td><td>17</td><td>290.0</td><td>40</td><td>750</td><td>25</td><td>5/60</td><td>30.00</td><td>2.58</td><td>69.6</td><td>1</td><td>1</td><td>0</td></tr>'
        ),
        url(query): bowling_result_page(
            [bowling_row(904, "Costly Bowler", "ENG", "2010-2020", 35, "33.00")]
        ),
    }
    source, client = await client_for(pages)

    async with client:
        result = await client.call_tool(
            "better_than_player",
            {
                "player_name": "Team Target",
                "format": "Test",
                "discipline": "bowling",
                "metrics": ["average"],
                "continent": "Asia",
                "period": {"kind": "all_time"},
            },
        )

    assert result.is_error is True
    assert source.requests == list(pages)
    assert "Team Target was not in the qualifying Statsguru rows." in result.content[0].text
    assert "team filter" not in result.content[0].text


async def test_comparison_finds_the_target_on_a_later_page_without_the_fallback() -> None:
    query = StatsguruQuery(
        **{
            "class": 3,
            "type": "batting",
            "host": 27,
            "qualifications": (Qualification(field="runs", minimum=1000),),
            "orderby": "batting_average",
            "size": 200,
        }
    )
    proof_query = query.model_copy(
        update={
            "qualifications": (
                *query.qualifications,
                Qualification(field="batting_average", minimum=Decimal("42.16")),
            )
        }
    )
    star = row(801, "Visiting Star", "VVV", "2018-2022", 30, 1830, "61.00", 1350, "135.55", 2, 9)
    target = row(777, "Target Batter", "TTT", "2016-2023", 40, 1265, "42.16", 1000, "126.50", 1, 8)
    home = row(804, "Home Opener", "UAE", "2016-2023", 52, 1450, "31.20", 1200, "120.83", 1, 8)
    pages = {
        player_search_url("Target Batter"): search_page(
            search_row("Target Batter", "TTT", 777, 3, "Twenty20 Internationals", "2016 - 2024")
        ),
        url(query): result_page([star], pages=2, total=202),
        url(query.model_copy(update={"page": 2})): result_page(
            [target, home], page=2, pages=2, total=202
        ),
        url(proof_query): result_page([star, target]),
    }
    source, client = await client_for(pages)

    async with client:
        result = await client.call_tool(
            "better_than_player",
            {
                "player_name": "Target Batter",
                "format": "T20I",
                "metrics": ["average"],
                "host_country": "United Arab Emirates",
                "period": {"kind": "all_time"},
            },
        )

    assert result.is_error is False
    assert source.requests == list(pages)
    assert [(row["player"], row["relation"]) for row in result.structured_content["rows"]] == [
        ("Visiting Star", "beats"),
        ("Target Batter", "target"),
    ]
    assert result.structured_content["proof"]["confirmed"] is True
    assert "Minimum: runs >= 1000." in result.structured_content["answer_markdown"]


async def test_batting_comparison_refuses_a_lower_floor_that_cannot_fit_the_page_limit() -> None:
    query = StatsguruQuery(
        **{
            "class": 3,
            "type": "batting",
            "host": 27,
            "qualifications": (Qualification(field="runs", minimum=1000),),
            "orderby": "batting_average",
            "size": 200,
        }
    )
    home = row(804, "Home Opener", "UAE", "2016-2023", 52, 1450, "31.20", 1200, "120.83", 1, 8)
    pages = {
        player_search_url("Target Batter"): search_page(
            search_row("Target Batter", "TTT", 777, 3, "Twenty20 Internationals", "2016 - 2024")
        ),
        url(query): result_page([home], pages=3, total=450),
        url(query.model_copy(update={"page": 2})): result_page([home], page=2, pages=3, total=450),
        url(query.model_copy(update={"page": 3})): result_page([home], page=3, pages=3, total=450),
    }
    source, client = await client_for(pages)

    async with client:
        result = await client.call_tool(
            "better_than_player",
            {
                "player_name": "Target Batter",
                "format": "T20I",
                "metrics": ["average"],
                "host_country": "United Arab Emirates",
                "period": {"kind": "all_time"},
            },
        )

    assert source.requests == list(pages)
    assert result.is_error is True
    assert (
        "That query is too broad: Target Batter was not in the qualifying Statsguru rows "
        "(runs >= 1000), and a lower minimum needs at least 3 more pages, but this call has "
        "1 of its 4 pages left; pass minimum to set one."
    ) in result.content[0].text


async def test_batting_comparison_lowered_floor_counts_every_result_page() -> None:
    default_query = StatsguruQuery(
        **{
            "class": 3,
            "type": "batting",
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
    target_page_url = PlayerPageSpec(
        player_id=777, **{"class": 3, "type": "batting", "host": 27}
    ).url(as_of=FakeClock().now().date())
    home = row(804, "Home Opener", "UAE", "2016-2023", 52, 1450, "31.20", 1200, "120.83", 1, 8)
    star = row(801, "Visiting Star", "VVV", "2018-2022", 12, 610, "61.00", 450, "135.55", 1, 5)
    target = row(777, "Target Batter", "TTT", "2016-2023", 15, 506, "42.16", 400, "126.50", 0, 3)
    search_url = player_search_url("Target Batter")
    source, client = await client_for(
        {
            search_url: search_page(
                search_row("Target Batter", "TTT", 777, 3, "Twenty20 Internationals", "2016 - 2024")
            ),
            url(default_query): result_page([home]),
            target_page_url: player_page(
                '<tr class="data1"><td>filtered</td><td>2016-2023</td><td>16</td><td>15</td><td>3</td><td>506</td><td>77*</td><td>42.16</td><td>400</td><td>126.50</td><td>0</td><td>3</td><td>0</td></tr>'
            ),
            url(lowered_query): result_page([star], pages=2, total=202),
            url(lowered_query.model_copy(update={"page": 2})): result_page(
                [target, home], page=2, pages=2, total=202
            ),
            url(proof_query): result_page([star], pages=2, total=201),
        }
    )

    async with client:
        result = await client.call_tool(
            "better_than_player",
            {
                "player_name": "Target Batter",
                "format": "T20I",
                "metrics": ["average"],
                "host_country": "United Arab Emirates",
                "period": {"kind": "all_time"},
            },
        )

    assert result.is_error is False
    assert source.requests == [
        search_url,
        url(default_query),
        target_page_url,
        url(lowered_query),
        url(lowered_query.model_copy(update={"page": 2})),
        url(proof_query),
    ]
    assert [(row["player"], row["relation"]) for row in result.structured_content["rows"]] == [
        ("Visiting Star", "beats"),
        ("Target Batter", "target"),
    ]
    assert result.structured_content["request_pages"] == 3
    proof = result.structured_content["proof"]
    assert proof["confirmed"] is False
    assert "confirmation needs 2 proof pages, over the 1-page limit" in proof["label"]


async def test_any_mode_short_answer_counts_beaters_on_each_metric() -> None:
    query = StatsguruQuery(
        **{
            "class": 3,
            "type": "batting",
            "qualifications": (Qualification(field="runs", minimum=1000),),
            "orderby": "batting_average",
            "size": 200,
        }
    )
    rows = [
        row(1, "Average Again", "AAA", "2016-2024", 50, 2500, "50.00", 2777, "90.02", 2, 9),
        row(2, "Both Better", "BBB", "2016-2024", 40, 1800, "45.00", 1285, "140.07", 1, 9),
        row(3, "Average Only", "CCC", "2016-2024", 38, 1520, "40.00", 1520, "100.00", 1, 8),
        row(777, "Target Batter", "TTT", "2016-2024", 40, 1558, "38.94", 1217, "128.02", 1, 8),
        row(4, "Strike Only", "DDD", "2016-2024", 50, 1500, "30.00", 1000, "150.00", 0, 7),
        row(5, "Neither", "EEE", "2016-2024", 50, 1000, "20.00", 900, "111.11", 0, 3),
    ]
    search_url = player_search_url("Target Batter")
    source, client = await client_for(
        {
            search_url: search_page(
                search_row("Target Batter", "TTT", 777, 3, "Twenty20 Internationals", "2016 - 2024")
            ),
            url(query): result_page(rows),
        }
    )

    async with client:
        result = await client.call_tool(
            "better_than_player",
            {
                "player_name": "Target Batter",
                "format": "T20I",
                "metrics": ["average", "strike_rate"],
                "match": "any",
                "period": {"kind": "all_time"},
            },
        )
        single = await client.call_tool(
            "better_than_player",
            {
                "player_name": "Target Batter",
                "format": "T20I",
                "metrics": ["average"],
                "match": "any",
                "period": {"kind": "all_time"},
            },
        )

    assert source.requests == [search_url, url(query)]
    assert result.structured_content["answer_markdown"].startswith(
        "4 player(s) beat Target Batter's displayed batting average or strike rate: "
        "3 on batting average and 2 on strike rate.\n"
    )
    assert single.structured_content["answer_markdown"].startswith(
        "3 player(s) beat Target Batter's displayed batting average.\n"
    )


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


# Issue #57: Statsguru lists two Test players as "Imran Khan" (PAK), so only player_id tells them
# apart. The figures, spans and match counts below are synthetic.
KHAN_DATES = {"kind": "dates", "start": "1981-09-17", "end": "1992-12-31"}


def khan_spec(player_id: int, **fields: object) -> PlayerPageSpec:
    return PlayerPageSpec(
        player_id=player_id,
        **{
            "class": 1,
            "type": "batting",
            "period": ResolvedPeriod(start="1981-09-17", end="1992-12-31"),
            **fields,
        },
    )


def khan_page(player_id: int = 40560) -> str:
    return named(
        player_page(
            '<tr class="data1"><td>unfiltered</td><td>1970-1991</td><td>80</td><td>120</td><td>20</td><td>3500</td><td>130</td><td>35.00</td><td>6000</td><td>58.33</td><td>5</td><td>15</td><td>7</td></tr>'
            '<tr class="data1"><td>filtered</td><td>1981-1991</td><td>40</td><td>60</td><td>20</td><td>2000</td><td>130</td><td>50.00</td><td>3500</td><td>57.14</td><td>4</td><td>12</td><td>3</td></tr>'
        ),
        player_id,
        "Imran Khan",
        "Test matches",
    )


def markdown_rows(text: str) -> list[list[str]]:
    return [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in text.splitlines()
        if line.startswith("| ") and not set(line.strip()) <= {"|", "-", " "}
    ]


async def test_same_name_clarification_then_player_id_returns_that_players_record() -> None:
    first_url = khan_spec(40560).url(as_of=FakeClock().now().date())
    second_url = khan_spec(316363).url(as_of=FakeClock().now().date())
    search_url = player_search_url("Imran Khan")
    source, client = await client_for(
        {
            search_url: search_page(
                search_row(
                    "Imran Khan", "PAK", 316363, 1, "Test matches", "2015/16 - 2020/21", matches=12
                ),
                search_row(
                    "Imran Khan", "PAK", 40560, 1, "Test matches", "1970 - 1990/91", matches=80
                ),
            ),
            first_url: khan_page(),
            second_url: named(
                player_page(
                    '<tr class="data1"><td>unfiltered</td><td>2015-2021</td><td>12</td><td>15</td><td>3</td><td>150</td><td>40</td><td>12.50</td><td>300</td><td>50.00</td><td>0</td><td>0</td><td>2</td></tr>'
                ),
                316363,
                "Imran Khan",
                "Test matches",
            ),
        }
    )

    async with client:
        clarify = await client.call_tool(
            "player_record", {"player_name": "Imran Khan", "format": "Test", "period": KHAN_DATES}
        )
        compare = await client.call_tool(
            "better_than_player",
            {"player_name": "Imran Khan", "format": "Test", "metrics": ["average"]},
        )
        assert source.requests == [search_url]
        source.requests.clear()
        record = await client.call_tool(
            "player_record", {"player_id": 40560, "format": "Test", "period": KHAN_DATES}
        )

    assert clarify.structured_content["status"] == "needs_clarification"
    assert clarify.structured_content["query"] == "Imran Khan"
    assert clarify.structured_content["candidates"] == [
        {
            "id": 40560,
            "name": "Imran Khan",
            "full_name": None,
            "country": ["PAK"],
            "formats": [
                {
                    "class": 1,
                    "label": "Test matches player",
                    "role": "player",
                    "span": "1970-1990/91",
                    "matches": 80,
                }
            ],
        },
        {
            "id": 316363,
            "name": "Imran Khan",
            "full_name": None,
            "country": ["PAK"],
            "formats": [
                {
                    "class": 1,
                    "label": "Test matches player",
                    "role": "player",
                    "span": "2015/16-2020/21",
                    "matches": 12,
                }
            ],
        },
    ]
    assert clarify.structured_content["hint"] == (
        "Call player_record again with player_id set to the chosen candidate's ID."
    )
    assert compare.structured_content["hint"] == (
        "Call better_than_player again with player_id set to the chosen candidate's ID."
    )
    for result in (clarify, compare):
        text = result.content[0].text
        assert result.structured_content["hint"] in text
        assert markdown_rows(text) == [
            ["ID", "Name", "Country", "Test span", "Test matches"],
            ["40560", "Imran Khan", "PAK", "1970-1990/91", "80"],
            ["316363", "Imran Khan", "PAK", "2015/16-2020/21", "12"],
        ]

    assert source.requests == [first_url]
    assert record.structured_content["status"] == "ok"
    assert record.structured_content["player"] == {
        "id": 40560,
        "name": "Imran Khan",
        "full_name": None,
        "country": [],
        "formats": [],
    }
    assert record.structured_content["row"]["Mat"] == 40
    assert record.structured_content["row"]["Runs"] == 2000
    assert record.structured_content["row"]["Ave"] == "50.00"
    assert record.structured_content["proof"]["url"] == first_url
    answer = record.structured_content["answer_markdown"]
    assert answer.startswith(
        "Imran Khan's record: matches 40, innings 60, runs 2000, average 50.00, hundreds 4, "
        "fifties 12, highest score 130."
    )
    assert "- [Imran Khan profile](https://stats.cricinfo.com/ci/content/player/40560.html)" in (
        answer
    )
    assert "follows player_id" not in answer


async def test_player_record_by_id_names_the_player_from_the_page_and_flags_a_clash() -> None:
    as_of = FakeClock().now().date()
    page_url = khan_spec(40560).url(as_of=as_of)
    kohli_url = PlayerPageSpec(player_id=253802, **{"class": 3, "type": "batting"}).url(as_of=as_of)
    unnamed_url = PlayerPageSpec(player_id=40560, **{"class": 2, "type": "batting"}).url(
        as_of=as_of
    )
    rows = (
        '<tr class="data1"><td>unfiltered</td><td>2010-2020</td><td>90</td><td>85</td><td>10</td><td>2500</td><td>94*</td><td>33.33</td><td>2000</td><td>125.00</td><td>0</td><td>20</td><td>3</td></tr>'
        '<tr class="data1"><td>filtered</td><td>2010-2020</td><td>90</td><td>85</td><td>10</td><td>2500</td><td>94*</td><td>33.33</td><td>2000</td><td>125.00</td><td>0</td><td>20</td><td>3</td></tr>'
    )
    source, client = await client_for(
        {
            page_url: khan_page(),
            kohli_url: named(player_page(rows), 253802, "V Kohli", "Twenty20 Internationals"),
            unnamed_url: player_page(rows),
        }
    )
    arguments = {"player_id": 40560, "format": "Test", "period": KHAN_DATES}

    async with client:
        clash = await client.call_tool("player_record", {**arguments, "player_name": "Babar Azam"})
        variants = [
            await client.call_tool("player_record", {**arguments, "player_name": name})
            for name in ("imran khan", "Imran", "Imran Khan Niazi")
        ]
        initials = await client.call_tool(
            "player_record", {"player_id": 253802, "player_name": "Virat Kohli", "format": "T20I"}
        )
        unnamed = await client.call_tool(
            "player_record", {"player_id": 40560, "player_name": "Imran Khan", "format": "ODI"}
        )

    assert source.requests == [page_url, kohli_url, unnamed_url]
    assert clash.structured_content["player"]["name"] == "Imran Khan"
    clash_answer = clash.structured_content["answer_markdown"]
    assert clash_answer.startswith("Imran Khan's record: matches 40,")
    assert (
        "- Player: player_id 40560 is Imran Khan on Statsguru, not 'Babar Azam'; "
        "the answer follows player_id.\n- Period: 1981-09-17 to 1992-12-31."
    ) in clash_answer
    for result in variants:
        assert result.structured_content["player"]["name"] == "Imran Khan"
        assert "follows player_id" not in result.structured_content["answer_markdown"]
    assert initials.structured_content["answer_markdown"].startswith("V Kohli's record:")
    assert "follows player_id" not in initials.structured_content["answer_markdown"]
    # Without a breadcrumb the ID labels the player, and there's no Statsguru name to clash with.
    assert unnamed.structured_content["player"]["name"] == "Player ID 40560"
    assert unnamed.structured_content["answer_markdown"].startswith("Player ID 40560's record:")
    assert "follows player_id" not in unnamed.structured_content["answer_markdown"]


@pytest.mark.parametrize(
    ("given", "fetched", "agree"),
    [
        ("Imran Khan", "Imran Khan", True),
        ("imran  khan", "Imran Khan", True),
        ("Imran", "Imran Khan", True),
        ("Imran Khan Niazi", "Imran Khan", True),
        ("James Anderson", "JM Anderson", True),
        ("Virat Kohli", "V Kohli", True),
        ("Virat", "V Kohli", True),
        ("Mahendra Singh Dhoni", "MS Dhoni", True),
        ("Inzamam", "Inzamam-ul-Haq", True),
        ("Babar Azam", "Imran Khan", False),
        ("Joe Root", "JM Anderson", False),
        ("Imran Khan", "IK Pathan", False),
    ],
)
def test_names_agree_unless_no_surname_like_token_is_shared(
    given: str, fetched: str, agree: bool
) -> None:
    assert names_agree(given, fetched) is agree


async def test_player_record_by_id_resolves_career_periods_without_a_search() -> None:
    form_url, form = career_form(40560, 1, "25 Nov 1971", "07 Jan 1992")
    spec = khan_spec(40560, period=ResolvedPeriod(start="1990-01-07", end="1992-01-07"))
    page_url = spec.url(as_of=FakeClock().now().date())
    source, client = await client_for({form_url: form, page_url: khan_page()})

    async with client:
        result = await client.call_tool(
            "player_record",
            {"player_id": 40560, "format": "Test", "period": {"kind": "last_years", "years": 2}},
        )

    assert source.requests == [form_url, page_url]
    assert result.structured_content["row"]["Ave"] == "50.00"
    assert result.structured_content["proof"]["url"] == page_url
    assert "Period: 1990-01-07 to 1992-01-07." in result.structured_content["answer_markdown"]


async def test_better_than_player_by_id_finds_the_target_row_without_a_search() -> None:
    period = ResolvedPeriod(start="1981-09-17", end="1992-12-31")
    base_query = StatsguruQuery(
        **{
            "class": 1,
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
                Qualification(field="batting_average", minimum="48.25"),
            )
        }
    )
    rows = [
        row(1101, "Better Batter", "AAA", "1982-1992", 90, 4500, "55.00", 9000, "50.00", 12, 20),
        row(316363, "Imran Khan", "PAK", "1983-1990", 30, 1100, "52.00", 2000, "55.00", 2, 6),
        row(40560, "Imran Khan", "PAK", "1981-1992", 60, 2000, "48.25", 4000, "50.00", 4, 12),
        row(1102, "Level Batter", "BBB", "1984-1991", 50, 1930, "48.25", 3900, "49.48", 3, 10),
        row(1103, "Lower Batter", "CCC", "1981-1989", 70, 2500, "40.00", 5200, "48.07", 3, 15),
    ]
    source, client = await client_for(
        {url(base_query): result_page(rows), url(proof_query): result_page(rows[:4])}
    )

    async with client:
        result = await client.call_tool(
            "better_than_player",
            {"player_id": 40560, "format": "Test", "metrics": ["average"], "period": KHAN_DATES},
        )
        clash = await client.call_tool(
            "better_than_player",
            {
                "player_id": 40560,
                "player_name": "Babar Azam",
                "format": "Test",
                "metrics": ["average"],
                "period": KHAN_DATES,
            },
        )

    assert source.requests == [url(base_query), url(proof_query)]
    structured = result.structured_content
    assert structured["player"]["id"] == 40560
    assert structured["player"]["name"] == "Imran Khan"
    assert [(row["player_id"], row["relation"]) for row in structured["rows"]] == [
        (1101, "beats"),
        (316363, "beats"),
        (40560, "target"),
        (1102, "level"),
    ]
    assert structured["proof"]["confirmed"] is True
    assert structured["answer_markdown"].startswith(
        "2 player(s) beat Imran Khan's displayed batting average. "
        "1 player(s) were level with Imran Khan.\n"
    )
    assert "follows player_id" not in structured["answer_markdown"]
    assert clash.structured_content["player"]["name"] == "Imran Khan"
    assert (
        "## Assumptions\n- Player: player_id 40560 is Imran Khan on Statsguru, not 'Babar Azam'; "
        "the answer follows player_id.\n- Minimum: runs >= 1000."
    ) in clash.structured_content["answer_markdown"]


async def test_better_than_player_by_id_names_a_missing_target_from_its_page() -> None:
    arguments, query, pages, (target_url, target_page) = team_filter_case("batting", 600)
    other = row(904, "Team Opener", "IND", "2016-2024", 30, 1100, "36.66", 846, "130.02", 1, 8)
    pages = {
        url(query(1000)): result_page([other]),
        target_url: named(target_page, 902, "Team Target", "Twenty20 Internationals"),
        url(query(600)): result_page([other]),
    }
    by_id = {key: value for key, value in arguments.items() if key != "player_name"}
    source, client = await client_for(pages)

    async with client:
        result = await client.call_tool("better_than_player", {**by_id, "player_id": 902})
        explicit = await client.call_tool(
            "better_than_player", {**by_id, "player_id": 902, "minimum": 600}
        )

    assert source.requests == list(pages)
    assert result.is_error is True
    assert (
        "Team Target (player ID 902) was not in the qualifying Statsguru T20I rows "
        "(runs >= 600). Team Target (player ID 902)'s runs for that team can't be read from "
        "the player page, which has no team filter; pass minimum to set a lower one."
    ) in result.content[0].text
    assert explicit.is_error is True
    assert "Player ID 902 was not in the qualifying Statsguru T20I rows." in (
        explicit.content[0].text
    )


async def test_player_id_without_a_record_in_the_format_is_a_clear_error() -> None:
    as_of = FakeClock().now().date()
    odi_url = PlayerPageSpec(player_id=316363, **{"class": 2, "type": "batting"}).url(as_of=as_of)
    unknown_url = PlayerPageSpec(player_id=999999999, **{"class": 1, "type": "batting"}).url(
        as_of=as_of
    )
    gone_url = PlayerPageSpec(player_id=999999998, **{"class": 1, "type": "bowling"}).url(
        as_of=as_of
    )
    form_url = "https://stats.cricinfo.com/ci/engine/player/316363.html?class=2;type=batting"
    innings_url = PlayerPageSpec(
        player_id=316363, **{"class": 2, "type": "batting", "view": "innings"}
    ).url()
    dated_url = khan_spec(316363, **{"class": 2}).url(as_of=as_of)
    dated_query = StatsguruQuery(
        **{
            "class": 2,
            "type": "batting",
            "period": ResolvedPeriod(start="1981-09-17", end="1992-12-31"),
            "qualifications": (Qualification(field="runs", minimum=1000),),
            "orderby": "batting_average",
            "size": 200,
        }
    )
    no_odis = no_records_player_page(316363, "Imran Khan", "One-Day Internationals")
    source, client = await client_for(
        {
            odi_url: no_odis,
            unknown_url: no_records_player_page(999999999, "", "Test matches"),
            gone_url: FetchResponse(url=gone_url, status_code=404, headers={}, text=""),
            form_url: named(
                '<html><body><form name="gurumenu"><input type="hidden" name="class" value="2">'
                "</form></body></html>",
                316363,
                "Imran Khan",
                "One-Day Internationals",
            ),
            innings_url: no_odis,
            url(dated_query): result_page(
                [
                    row(
                        1101,
                        "Better Batter",
                        "AAA",
                        "1982-1992",
                        90,
                        4500,
                        "55.00",
                        9000,
                        "50.00",
                        1,
                        2,
                    )
                ]
            ),
            dated_url: no_odis,
        }
    )

    async with client:
        record = await client.call_tool("player_record", {"player_id": 316363, "format": "ODI"})
        unknown = await client.call_tool(
            "player_record", {"player_id": 999999999, "format": "Test"}
        )
        gone = await client.call_tool(
            "player_record", {"player_id": 999999998, "format": "Test", "discipline": "bowling"}
        )
        career_record = await client.call_tool(
            "player_record",
            {"player_id": 316363, "format": "ODI", "period": {"kind": "last_years", "years": 2}},
        )
        career = await client.call_tool(
            "better_than_player", {"player_id": 316363, "format": "ODI", "metrics": ["average"]}
        )
        dated = await client.call_tool(
            "better_than_player",
            {"player_id": 316363, "format": "ODI", "metrics": ["average"], "period": KHAN_DATES},
        )

    results = (record, unknown, gone, career_record, career, dated)
    assert [result.is_error for result in results] == [True] * 6
    no_odi_record = "Player ID 316363 (Imran Khan) has no ODI batting record on Statsguru."
    assert no_odi_record in record.content[0].text
    assert "Player ID 999999999 has no Test batting record on Statsguru." in unknown.content[0].text
    assert "Player ID 999999998 has no Test bowling record on Statsguru." in gone.content[0].text
    # Career-based periods read the form page (no spanmin0 for ODIs), then the innings list.
    assert no_odi_record in career_record.content[0].text
    assert no_odi_record in career.content[0].text
    assert no_odi_record in dated.content[0].text
    assert source.requests == [
        odi_url,
        unknown_url,
        gone_url,
        form_url,
        innings_url,
        url(dated_query),
        dated_url,
    ]


async def test_errors_by_id_never_name_the_player_from_player_name() -> None:
    as_of = FakeClock().now().date()
    odi_url = PlayerPageSpec(player_id=316363, **{"class": 2, "type": "batting"}).url(as_of=as_of)
    gone_url = PlayerPageSpec(player_id=999999998, **{"class": 1, "type": "bowling"}).url(
        as_of=as_of
    )
    arguments, query, _pages, (target_url, target_page) = team_filter_case("batting", 600)
    other = row(904, "Team Opener", "IND", "2016-2024", 30, 1100, "36.66", 846, "130.02", 1, 8)
    pages = {
        odi_url: no_records_player_page(316363, "Imran Khan", "One-Day Internationals"),
        gone_url: FetchResponse(url=gone_url, status_code=404, headers={}, text=""),
        url(query(1000)): result_page([other]),
        target_url: named(target_page, 902, "Team Target", "Twenty20 Internationals"),
        url(query(600)): result_page([other]),
    }
    comparison = {**arguments, "player_name": "Babar Azam", "player_id": 902}
    source, client = await client_for(pages)

    async with client:
        no_odis = await client.call_tool(
            "player_record", {"player_name": "Babar Azam", "player_id": 316363, "format": "ODI"}
        )
        gone = await client.call_tool(
            "player_record",
            {
                "player_name": "Babar Azam",
                "player_id": 999999998,
                "format": "Test",
                "discipline": "bowling",
            },
        )
        team = await client.call_tool("better_than_player", comparison)
        explicit = await client.call_tool("better_than_player", {**comparison, "minimum": 600})

    results = (no_odis, gone, team, explicit)
    assert [result.is_error for result in results] == [True] * 4
    texts = [result.content[0].text for result in results]
    assert "Player ID 316363 (Imran Khan) has no ODI batting record on Statsguru." in texts[0]
    assert "Player ID 999999998 has no Test bowling record on Statsguru." in texts[1]
    assert "Team Target (player ID 902) was not in the qualifying Statsguru T20I rows" in texts[2]
    assert "Player ID 902 was not in the qualifying Statsguru T20I rows." in texts[3]
    assert not any("Babar" in text for text in texts)
    assert source.requests == list(pages)


async def test_answer_tools_need_a_player_name_or_player_id() -> None:
    source, client = await client_for({})

    async with client:
        record = await client.call_tool("player_record", {"format": "Test"})
        blank = await client.call_tool("player_record", {"player_name": "  ", "format": "Test"})
        compare = await client.call_tool(
            "better_than_player", {"format": "Test", "metrics": ["runs"]}
        )
        zero = await client.call_tool("player_record", {"player_id": 0, "format": "Test"})

    for result in (record, blank, compare):
        assert result.is_error is True
        assert (
            "player_name or player_id is required; player_id is the ID from find_player "
            "or a needs_clarification candidate."
        ) in result.content[0].text
    assert zero.is_error is True
    assert "player_id" in zero.content[0].text
    assert source.requests == []


# Issue #59: a summary page's "Career summary" table groups the page's matches by opposition, host
# country, continent, year and more, with a blank row between groupings. Each row's innings link
# is the page's own query with that grouping's filter set to the row's value (R6). The players,
# IDs and figures below are synthetic.
SPLIT_BATTING_COLUMNS = tuple("Span Mat Inns NO Runs HS Ave BF SR 100 50 0".split())
SPLIT_BOWLING_COLUMNS = tuple("Span Mat Inns Overs Mdns Runs Wkts BBI BBM Ave Econ SR 5 10".split())
CENTURION_ID = 900001
SWINGER_ID = 900002
CENTURION_CAREER = "2001-2012|30|52|4|2400|210|50|4800|50|7|10|3"
CENTURION_GROUPS = [
    [
        ("v England", "opposition=1", "2001-2012|10|18|1|850|210|50|1700|50|3|3|1"),
        ("v India", "opposition=6", "2003-2011|12|20|2|900|150|50|1800|50|2|4|1"),
        ("v Sri Lanka", "opposition=8", "2002-2012|8|14|1|650|120*|50|1300|50|2|3|1"),
    ],
    [
        ("in England", "host=1", "2001-2011|6|11|0|500|210|45.45|1000|50|2|2|1"),
        ("in India", "host=6", "2003-2011|9|15|1|420|95|30|900|46.66|0|4|1"),
        ("in Sri Lanka", "host=8", "2002-2012|5|9|1|380|120*|47.5|760|50|1|2|0"),
        ("in U.A.E.", "host=27", "2004-2012|10|17|2|1100|180|73.33|2140|51.4|4|2|1"),
    ],
    [
        ("in Asia", "continent=2", "2002-2012|24|41|4|1900|180|51.35|3800|50|5|8|2"),
        ("in Europe", "continent=4", "2001-2011|6|11|0|500|210|45.45|1000|50|2|2|1"),
    ],
    [
        ("home", "home_or_away=1", "2002-2012|5|9|1|380|120*|47.5|760|50|1|2|0"),
        ("away", "home_or_away=2", "2001-2011|15|26|1|920|210|36.8|1900|48.42|2|6|2"),
        ("neutral", "home_or_away=3", "2004-2012|10|17|2|1100|180|73.33|2140|51.4|4|2|1"),
    ],
    [
        ("year 2001", "year=2001", "|10|17|1|800|210|50|1600|50|3|3|1"),
        ("year 2005", "year=2005", "|12|21|2|700|99|36.84|1500|46.66|0|5|1"),
        ("year 2012", "year=2012", "|8|14|1|900|180|69.23|1700|52.94|4|2|1"),
    ],
    [
        ("season 2001", "season=2001", "|10|17|1|800|210|50|1600|50|3|3|1"),
        ("season 2004/05", "season=2004%2F05", "|20|35|3|1600|180|50|3200|50|4|7|2"),
    ],
    [
        (
            "won batting first",
            "batting_fielding_first=1;result=1",
            "2001-2012|9|16|2|900|210|64.28|1700|52.94|3|3|1",
        )
    ],
]


def split_spec(player_id: int = CENTURION_ID, **fields: object) -> PlayerPageSpec:
    return PlayerPageSpec(player_id=player_id, **{"class": 1, "type": "batting", **fields})


def split_cells(values: str) -> str:
    return "".join(f"<td>{value}</td>" for value in values.split("|"))


def summary_page(
    spec: PlayerPageSpec,
    name: str,
    career: list[tuple[str, str]],
    groups: list[list[tuple[str, str, str]]],
    columns: tuple[str, ...] = SPLIT_BATTING_COLUMNS,
) -> str:
    path, query = (
        spec.url(as_of=FakeClock().now().date()).split("stats.cricinfo.com", 1)[1].split("?", 1)
    )
    params = query.split(";")

    def innings_link(setting: str) -> str:
        settings = setting.split(";")
        keys = {item.split("=", 1)[0] for item in settings}
        kept = [param for param in params if param.split("=", 1)[0] not in keys]
        return f"{path}?{';'.join(sorted([*kept, *settings, 'view=innings']))}"

    heads = "".join(f"<th>{column}</th>" for column in columns)
    grouped = f'<tr class="data1"><td colspan="{len(columns) + 2}"><br></td></tr>'.join(
        "".join(
            f'<tr class="data1"><td class="left" nowrap><b>{label}</b></td>{split_cells(values)}'
            f'<td><a href="{innings_link(setting)}" title="view all innings for this row"><img alt="view innings"></a></td></tr>'
            for label, setting, values in group
        )
        for group in groups
    )
    sort = f"{path}?{';'.join(sorted([*params, 'orderby=default', 'orderbyad=reverse']))}"
    averages = "".join(
        f'<tr class="data1"><td>{label}</td>{split_cells(values)}</tr>' for label, values in career
    )
    return named(
        f"""
        <html><body>
        <table class="engineTable"><caption>Career averages</caption>
        <tr class="head"><th></th>{heads}</tr>
        {averages}
        </table>
        <table class="engineTable"><caption>Career summary</caption>
        <thead><tr class="headlinks"><th><a href="{sort}">Grouping</a></th>{heads}<th></th></tr></thead>
        <tbody>{grouped}</tbody>
        </table>
        </body></html>
        """,
        spec.player_id,
        name,
        "Test matches",
    )


def centurion_pages() -> dict[str, str]:
    spec = split_spec()
    return {
        player_search_url("Sample Centurion"): search_page(
            search_row(
                "Sample Centurion", "PAK", CENTURION_ID, 1, "Test matches", "2001 - 2012", 30
            )
        ),
        spec.url(as_of=FakeClock().now().date()): summary_page(
            spec,
            "Sample Centurion",
            [("unfiltered", CENTURION_CAREER), ("filtered", CENTURION_CAREER)],
            CENTURION_GROUPS,
        ),
    }


async def test_split_by_host_counts_hundreds_in_host_countries_not_continents() -> None:
    page_url = split_spec().url(as_of=FakeClock().now().date())
    source, client = await client_for(centurion_pages())

    async with client:
        result = await client.call_tool(
            "player_record",
            {"player_name": "Sample Centurion", "format": "Test", "split_by": "host"},
        )

    structured = result.structured_content
    split = structured["split"]
    assert [(row["name"], row["host"], row["Mat"], row["100"]) for row in split["rows"]] == [
        ("England", 1, 6, 2),
        ("India", 6, 9, 0),
        ("Sri Lanka", 8, 5, 1),
        ("U.A.E.", 27, 10, 4),
    ]
    assert split["rows"][2] == {
        "name": "Sri Lanka",
        "label": "in Sri Lanka",
        "host": 8,
        "Span": "2002-2012",
        "Mat": 5,
        "Inns": 9,
        "NO": 1,
        "Runs": 380,
        "HS": "120*",
        "Ave": "47.5",
        "BF": 760,
        "SR": 50,
        "100": 1,
        "50": 2,
        "0": 0,
    }
    assert {key: value for key, value in split.items() if key != "rows"} == {
        "by": "host",
        "groups": 4,
        "with_hundreds": 3,
        "without_hundreds": ["India"],
        "matches": 30,
    }
    assert structured["row"]["Mat"] == 30
    assert structured["proof"]["url"] == page_url
    assert structured["proof"]["confirmed"] is True
    assert structured["proof"]["formula"] is None
    # Like the plain record, the proof counts the page's "Career averages" rows.
    assert structured["proof"]["row_count"] == 2
    answer = structured["answer_markdown"]
    assert answer.startswith(
        "Sample Centurion's Test batting split by host country: at least one hundred in 3 of 4 "
        "host countries; none in India.\n"
    )
    assert markdown_rows(answer) == [
        ["Host country", "Mat", "Inns", "Runs", "Ave", "SR", "100", "50"],
        ["England", "6", "11", "500", "45.45", "50", "2", "2"],
        ["India", "9", "15", "420", "30", "46.66", "0", "4"],
        ["Sri Lanka", "5", "9", "380", "47.5", "50", "1", "2"],
        ["U.A.E.", "10", "17", "1100", "73.33", "51.4", "4", "2"],
    ]
    assert "Asia" not in answer
    assert "- Split: the 4 rows by host country add up to the record's 30 matches" in answer
    assert "check players one at a time" in answer
    assert f"- Answer proof: [Test player batting for {CENTURION_ID}," in answer
    assert source.requests == [player_search_url("Sample Centurion"), page_url]


async def test_split_by_opposition_and_year_read_their_own_grouped_rows() -> None:
    _source, client = await client_for(centurion_pages())

    async with client:
        opposition, year, continent = [
            await client.call_tool(
                "player_record",
                {"player_name": "Sample Centurion", "format": "Test", "split_by": split_by},
            )
            for split_by in ("opposition", "year", "continent")
        ]

    rows = opposition.structured_content["split"]["rows"]
    assert [(row["name"], row["opposition"], row["100"]) for row in rows] == [
        ("England", 1, 3),
        ("India", 6, 2),
        ("Sri Lanka", 8, 2),
    ]
    assert opposition.structured_content["answer_markdown"].startswith(
        "Sample Centurion's Test batting split by opposition: at least one hundred against all 3 "
        "oppositions.\n"
    )
    rows = year.structured_content["split"]["rows"]
    assert [(row["name"], row["year"], row["Span"], row["100"]) for row in rows] == [
        ("2001", 2001, None, 3),
        ("2005", 2005, None, 0),
        ("2012", 2012, None, 4),
    ]
    assert year.structured_content["split"]["without_hundreds"] == ["2005"]
    assert year.structured_content["answer_markdown"].startswith(
        "Sample Centurion's Test batting split by year: at least one hundred in 2 of 3 years; "
        "none in 2005.\n"
    )
    assert markdown_rows(year.structured_content["answer_markdown"])[0][0] == "Year"
    rows = continent.structured_content["split"]["rows"]
    assert [(row["name"], row["continent"], row["Mat"]) for row in rows] == [
        ("Asia", 2, 24),
        ("Europe", 4, 6),
    ]


async def test_split_by_host_for_bowling_counts_five_wicket_hauls() -> None:
    spec = split_spec(SWINGER_ID, type="bowling")
    page_url = spec.url(as_of=FakeClock().now().date())
    career = "2005-2015|20|38|700.0|150|2000|80|6/40|9/90|25|2.85|52.5|4|1"
    england = "2005-2015|12|23|420.0|100|1100|50|6/40|9/90|22|2.61|50.4|3|1"
    india = "2008-2012|4|8|150.0|25|450|10|3/50|4/90|45|3|90|0|0"
    new_zealand = "2007-2013|4|7|130.0|25|450|20|5/60|7/100|22.5|3.46|39|1|0"
    page = summary_page(
        spec,
        "Sample Swinger",
        [("unfiltered", career), ("filtered", career)],
        [
            [
                ("in England", "host=1", england),
                ("in India", "host=6", india),
                ("in New Zealand", "host=5", new_zealand),
            ],
            [
                ("in Asia", "continent=2", india),
                ("in Europe", "continent=4", england),
                ("in Oceania", "continent=5", new_zealand),
            ],
        ],
        SPLIT_BOWLING_COLUMNS,
    )
    source, client = await client_for({page_url: page})

    async with client:
        result = await client.call_tool(
            "player_record",
            {
                "player_id": SWINGER_ID,
                "format": "Test",
                "discipline": "bowling",
                "split_by": "host",
            },
        )

    split = result.structured_content["split"]
    assert [(row["name"], row["Wkts"], row["5"]) for row in split["rows"]] == [
        ("England", 50, 3),
        ("India", 10, 0),
        ("New Zealand", 20, 1),
    ]
    assert split["with_five_wicket_hauls"] == 2
    assert split["without_five_wicket_hauls"] == ["India"]
    answer = result.structured_content["answer_markdown"]
    assert answer.startswith(
        "Sample Swinger's Test bowling split by host country: at least one five-wicket haul in "
        "2 of 3 host countries; none in India.\n"
    )
    assert markdown_rows(answer)[:2] == [
        ["Host country", "Mat", "Wkts", "Ave", "Econ", "SR", "5"],
        ["England", "12", "50", "22", "2.61", "50.4", "3"],
    ]
    assert "Counted the host countries with at least one five-wicket haul" in answer
    assert source.requests == [page_url]


@pytest.mark.parametrize(
    "arguments",
    [{"player_id": CENTURION_ID}, {"player_id": CENTURION_ID, "player_name": "Sample Centurion"}],
)
async def test_split_by_with_player_id_reads_only_the_player_page(
    arguments: dict[str, object],
) -> None:
    page_url = split_spec().url(as_of=FakeClock().now().date())
    source, client = await client_for(centurion_pages())

    async with client:
        result = await client.call_tool(
            "player_record", {**arguments, "format": "Test", "split_by": "host"}
        )

    assert source.requests == [page_url]
    assert result.structured_content["player"]["name"] == "Sample Centurion"
    assert result.structured_content["split"]["with_hundreds"] == 3
    assert result.structured_content["answer_markdown"].startswith(
        "Sample Centurion's Test batting split by host country:"
    )


async def test_split_by_makes_no_extra_request() -> None:
    pages = centurion_pages()
    arguments = {"player_name": "Sample Centurion", "format": "Test"}
    plain_source, plain_client = await client_for(pages)
    split_source, split_client = await client_for(pages)

    async with plain_client:
        plain = await plain_client.call_tool("player_record", arguments)
    async with split_client:
        split = await split_client.call_tool("player_record", {**arguments, "split_by": "host"})
        split_requests = list(split_source.requests)
        split_source.requests.clear()
        # The record without split_by then comes from the page the split already read.
        again = await split_client.call_tool("player_record", arguments)

    assert split_requests == plain_source.requests == list(pages)
    assert split_source.requests == []
    split_proof = dict(split.structured_content["proof"])
    plain_proof = dict(plain.structured_content["proof"])
    assert (
        split_proof.pop("label")
        == f"{plain_proof.pop('label')}, Career summary rows by host country"
    )
    assert split_proof == plain_proof
    assert split.structured_content["row"] == plain.structured_content["row"]
    assert again.structured_content["row"] == plain.structured_content["row"]
    assert "split" not in plain.structured_content


async def test_split_by_on_filtered_pages_uses_the_filtered_grouped_rows() -> None:
    as_of = FakeClock().now().date()
    dated = split_spec(period=ResolvedPeriod(start="2005-01-01", end="2012-12-31"))
    uae = split_spec(host=27)
    filtered = "2005-2012|20|35|3|1600|180|50|3200|50|4|7|2"
    in_uae = "2004-2012|10|17|2|1100|180|73.33|2140|51.4|4|2|1"
    pages = {
        dated.url(as_of=as_of): summary_page(
            dated,
            "Sample Centurion",
            [("unfiltered", CENTURION_CAREER), ("filtered", filtered)],
            [
                [
                    ("in India", "host=6", "2005-2011|9|15|1|420|95|30|900|46.66|0|4|1"),
                    ("in U.A.E.", "host=27", "2005-2012|11|20|2|1180|180|65.55|2300|51.3|4|3|1"),
                ],
                [
                    ("year 2005", "year=2005", "|12|21|2|700|99|36.84|1500|46.66|0|5|1"),
                    ("year 2012", "year=2012", "|8|14|1|900|180|69.23|1700|52.94|4|2|1"),
                ],
            ],
        ),
        # Filtered to one host, the U.A.E. row's link is the page's own query.
        uae.url(as_of=as_of): summary_page(
            uae,
            "Sample Centurion",
            [("unfiltered", CENTURION_CAREER), ("filtered", in_uae)],
            [
                [
                    ("v England", "opposition=1", "2004-2012|4|7|1|500|180|83.33|900|55.55|2|1|0"),
                    ("v India", "opposition=6", "2006-2011|6|10|1|600|150|66.66|1240|48.38|2|1|1"),
                ],
                [("in U.A.E.", "host=27", in_uae)],
                [("in Asia", "continent=2", in_uae)],
            ],
        ),
    }
    source, client = await client_for(pages)

    async with client:
        years = await client.call_tool(
            "player_record",
            {
                "player_id": CENTURION_ID,
                "format": "Test",
                "period": {"kind": "dates", "start": "2005-01-01", "end": "2012-12-31"},
                "split_by": "year",
            },
        )
        hosts = await client.call_tool(
            "player_record",
            {
                "player_id": CENTURION_ID,
                "format": "Test",
                "host_country": "UAE",
                "split_by": "host",
            },
        )
        continents = await client.call_tool(
            "player_record",
            {
                "player_id": CENTURION_ID,
                "format": "Test",
                "host_country": "UAE",
                "split_by": "continent",
            },
        )

    assert source.requests == list(pages)
    assert [row["year"] for row in years.structured_content["split"]["rows"]] == [2005, 2012]
    assert years.structured_content["split"]["matches"] == 20
    answer = years.structured_content["answer_markdown"]
    assert answer.startswith(
        "Sample Centurion's Test batting split by year: at least one hundred in 1 of 2 years; "
        "none in 2005.\n"
    )
    assert "- Period: 2005-01-01 to 2012-12-31." in answer
    assert "- Split: the 2 rows by year add up to the record's 20 matches" in answer
    assert [row["name"] for row in hosts.structured_content["split"]["rows"]] == ["U.A.E."]
    assert hosts.structured_content["answer_markdown"].startswith(
        "Sample Centurion's Test batting split by host country: at least one hundred in the "
        "only host country, U.A.E.\n"
    )
    assert [row["name"] for row in continents.structured_content["split"]["rows"]] == ["Asia"]


async def test_split_by_refuses_grouped_rows_that_do_not_add_up_to_the_record() -> None:
    dated = split_spec(period=ResolvedPeriod(start="2005-01-01", end="2012-12-31"))
    # The record is filtered to 20 matches, but these grouped rows cover all 30.
    page = summary_page(
        dated,
        "Sample Centurion",
        [
            ("unfiltered", CENTURION_CAREER),
            ("filtered", "2005-2012|20|35|3|1600|180|50|3200|50|4|7|2"),
        ],
        CENTURION_GROUPS,
    )
    source, client = await client_for({dated.url(as_of=FakeClock().now().date()): page})

    async with client:
        result = await client.call_tool(
            "player_record",
            {
                "player_id": CENTURION_ID,
                "format": "Test",
                "period": {"kind": "dates", "start": "2005-01-01", "end": "2012-12-31"},
                "split_by": "host",
            },
        )

    assert result.is_error is True
    assert (
        "Statsguru's rows by host country add up to 30 matches, not the record's 20, so crickey "
        "can't confirm they cover the same filters and period. Call player_record without "
        "split_by, or once per host country with host_country."
    ) in result.content[0].text
    assert len(source.requests) == 1


async def test_split_by_rejects_unknown_groupings_before_any_request() -> None:
    source, client = await client_for(centurion_pages())

    async with client:
        results = [
            await client.call_tool(
                "player_record",
                {"player_name": "Sample Centurion", "format": "Test", "split_by": value},
            )
            for value in ("ground", "season", "country")
        ]

    for value, result in zip(("ground", "season", "country"), results, strict=True):
        assert result.is_error is True
        assert (
            f"split_by must be host, opposition, year or continent (got '{value}')."
            in result.content[0].text
        )
    assert source.requests == []


async def test_split_by_with_no_matching_record_reports_no_matches() -> None:
    spec = split_spec(host=6)
    page_url = spec.url(as_of=FakeClock().now().date())
    # Filtered to a host the player never played in: no filtered row and no grouped rows.
    page = named(
        player_page(
            '<tr class="data1"><td>unfiltered</td><td>2001-2012</td><td>30</td><td>52</td><td>4</td><td>2400</td><td>210</td><td>50</td><td>4800</td><td>50</td><td>7</td><td>10</td><td>3</td></tr>'
        ),
        CENTURION_ID,
        "Sample Centurion",
        "Test matches",
    )
    source, client = await client_for({page_url: page})

    async with client:
        result = await client.call_tool(
            "player_record",
            {
                "player_id": CENTURION_ID,
                "format": "Test",
                "host_country": "India",
                "split_by": "year",
            },
        )

    assert result.structured_content["status"] == "no_matches"
    assert result.structured_content["split"] is None
    assert source.requests == [page_url]


async def test_split_by_without_the_counted_column_does_not_claim_a_count() -> None:
    spec = split_spec(SWINGER_ID, type="bowling")
    page_url = spec.url(as_of=FakeClock().now().date())
    career = "2005-2015|20|80|25"
    page = summary_page(
        spec,
        "Sample Swinger",
        [("unfiltered", career), ("filtered", career)],
        [
            [
                ("in England", "host=1", "2005-2015|12|50|22"),
                ("in India", "host=6", "2008-2012|8|30|30"),
            ]
        ],
        ("Span", "Mat", "Wkts", "Ave"),
    )
    _source, client = await client_for({page_url: page})

    async with client:
        result = await client.call_tool(
            "player_record",
            {
                "player_id": SWINGER_ID,
                "format": "Test",
                "discipline": "bowling",
                "split_by": "host",
            },
        )

    split = result.structured_content["split"]
    assert "with_five_wicket_hauls" not in split
    assert split["groups"] == 2
    answer = result.structured_content["answer_markdown"]
    assert answer.startswith(
        "Sample Swinger's Test bowling split by host country: 2 host countries.\n"
    )
    assert "Counted" not in answer
    assert markdown_rows(answer)[0] == ["Host country", "Mat", "Wkts", "Ave"]
