from __future__ import annotations

# ruff: noqa: E501
import asyncio
from datetime import UTC, date, datetime, timedelta
from typing import Any

from crickey.fetcher import FetchResponse
from crickey.query import PlayerPageSpec, Qualification, ResolvedPeriod, StatsguruQuery

AS_OF = date(2026, 10, 4)


class E2EClock:
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


def player_search_url(search: str) -> str:
    return (
        "https://stats.cricinfo.com/ci/engine/stats/analysis.html?"
        f"search={search.replace(' ', '+')};template=analysis"
    )


def results_url(query: StatsguruQuery, *, page: int | None = None) -> str:
    if page is not None:
        query = query.model_copy(update={"page": page})
    return query.results_url(as_of=AS_OF)


def player_search_page(*rows: str) -> str:
    return "<html><body><table>" + "".join(rows) + "</table></body></html>"


def player_search_row(
    name: str, country: str, player_id: int, class_id: int, label: str, span: str
) -> str:
    return (
        f"<tr><td>{name}</td><td>{country}</td><td>"
        f'<a href="/ci/engine/player/{player_id}.html?class={class_id};type=allround">'
        f"{label} player</a> ({span}, 145 matches)</td></tr>"
    )


def career_form(player_id: int, class_id: int, start: str, end: str) -> tuple[str, str]:
    form_url = f"https://stats.cricinfo.com/ci/engine/player/{player_id}.html?class={class_id};type=batting"
    return (
        form_url,
        (
            '<form name="gurumenu">'
            f'<input type="hidden" name="spanmin0" value="{start}">'
            f'<input type="hidden" name="spanmax0" value="{end}">'
            "</form>"
        ),
    )


def batting_row(
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


def bowling_page() -> str:
    return """
    <html><body>
    <table class="engineTable"><caption>Overall figures</caption>
    <tr><th>Player</th><th>Span</th><th>Mat</th><th>Overs</th><th>Wkts</th></tr>
    <tr class="data1"><td><a href="/ci/content/player/793463.html">Rashid Khan</a> (AFG)</td><td>2015-2026</td><td>118</td><td>449.5</td><td>197</td></tr>
    </table>
    <table><tr><td>Page <b>1</b> of <b>1</b></td><td>Showing <b>1</b> - <b>1</b> of <b>1</b></td></tr></table>
    <table class="engineTable"><tr class="data2"><td><b>Statsguru includes the following current or recent ODIs:</b></td></tr>
    <tr class="data2"><td>Example XI v Sample XI at Testville, 1st ODI, Oct 3, 2026 [<a href="/ci/engine/match/1.html">ODI # 1</a>]</td></tr>
    </table>
    </body></html>
    """


def player_page(row_html: str) -> str:
    return f"""
    <html><body>
    <table class="engineTable"><caption>Career averages</caption>
    <tr><th></th><th>Span</th><th>Mat</th><th>Inns</th><th>NO</th><th>Runs</th><th>HS</th><th>Ave</th><th>BF</th><th>SR</th><th>100</th><th>50</th><th>0</th></tr>
    {row_html}
    </table>
    <table class="engineTable"><tr class="data2"><td><b>Statsguru includes the following current or recent ODIs:</b></td></tr>
    <tr class="data2"><td>Example XI v Sample XI at Testville, 1st ODI, Oct 3, 2026 [<a href="/ci/engine/match/1.html">ODI # 1</a>]</td></tr>
    </table>
    </body></html>
    """


def _babar_search_rows() -> tuple[str, ...]:
    return (
        player_search_row("Babar Azam", "PAK", 348144, 1, "Test matches", "2016 - 2026"),
        player_search_row("Babar Azam", "PAK", 348144, 2, "One-Day Internationals", "2015 - 2026"),
        player_search_row("Babar Azam", "PAK", 348144, 3, "Twenty20 Internationals", "2016 - 2026"),
    )


def happy_pages() -> dict[str, FetchResponse | str]:
    pages: dict[str, FetchResponse | str] = {
        player_search_url("Babar Azam"): player_search_page(*_babar_search_rows()),
        player_search_url("Babar"): player_search_page(
            *_babar_search_rows(),
            player_search_row(
                "Babar Hayat", "HKG", 539305, 3, "Twenty20 Internationals", "2014 - 2026"
            ),
        ),
        "https://stats.cricinfo.com/ci/engine/stats/index.html?class=1;filter=advanced;type=batting": """
        <form name="gurumenu">
        <select name="ground">
        <option value="701">UAE: Dubai Sports City Cricket Stadium</option>
        <option value="702">UAE: ICC Academy, Dubai</option>
        </select>
        </form>
        """,
    }
    leaderboard_query = StatsguruQuery(
        **{
            "class": 2,
            "type": "batting",
            "qualifications": (Qualification(field="hundreds", minimum=10),),
            "orderby": "hundreds",
            "orderbyad": "reverse",
            "size": 200,
        }
    )
    pages[results_url(leaderboard_query)] = result_page(
        [
            batting_row(1, "Alpha", "AAA", "2010-2020", 80, 4000, "50.00", 4500, "88.88", 12, 20),
            batting_row(
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
        ],
        total=2,
    )
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
    comparison_rows = [
        batting_row(
            1001, "Karanbir Singh", "AUT", "2024-2025", 40, 1721, "47.8", 1017, "169.22", 2, 10
        ),
        batting_row(253802, "V Kohli", "IND", "2017-2024", 70, 2531, "44.4", 1833, "138.07", 1, 20),
        batting_row(
            348144, "Babar Azam", "PAK", "2016-2026", 136, 4596, "38.94", 3590, "128.02", 3, 39
        ),
    ]
    form_url, form = career_form(348144, 3, "07 Sep 2016", "24 Feb 2026")
    pages[form_url] = form
    pages[results_url(base_query)] = result_page(comparison_rows, total=3)
    pages[results_url(proof_query)] = result_page(comparison_rows, total=3)
    record_spec = PlayerPageSpec(player_id=348144, **{"class": 2, "type": "batting", "trophy": 12})
    pages[record_spec.url(as_of=AS_OF)] = player_page(
        '<tr class="data1"><td>unfiltered</td><td>2015-2026</td><td>143</td><td>140</td><td>16</td><td>6626</td><td>158</td><td>53.43</td><td>7652</td><td>86.59</td><td>20</td><td>38</td><td>5</td></tr>'
        '<tr class="data1"><td>filtered</td><td>2019-2023</td><td>17</td><td>17</td><td>2</td><td>794</td><td>101*</td><td>52.93</td><td>926</td><td>85.74</td><td>1</td><td>7</td><td>0</td></tr>'
    )
    query_stats = StatsguruQuery(
        **{
            "class": 2,
            "type": "bowling",
            "qualifications": (Qualification(field="wickets", minimum=1),),
            "orderby": "wickets",
            "orderbyad": "reverse",
        }
    )
    pages[results_url(query_stats)] = bowling_page()
    return pages


def block_pages() -> dict[str, FetchResponse | str]:
    pages = {
        player_search_url("Babar Azam"): player_search_page(*_babar_search_rows()),
    }
    blocked_query = StatsguruQuery(
        **{
            "class": 2,
            "type": "batting",
            "qualifications": (Qualification(field="hundreds", minimum=10),),
            "orderby": "hundreds",
            "orderbyad": "reverse",
            "size": 200,
        }
    )
    url = results_url(blocked_query)
    pages[url] = FetchResponse(url=url, status_code=403, headers={}, text="<html>Statsguru</html>")
    return pages


def retry_after_pages() -> dict[str, FetchResponse | str]:
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
    url = results_url(query)
    return {
        url: FetchResponse(
            url=url,
            status_code=429,
            headers={"Retry-After": "9999"},
            text="<html>Statsguru</html>",
        )
    }


def broad_pages() -> dict[str, FetchResponse | str]:
    query = StatsguruQuery(
        **{
            "class": 2,
            "type": "batting",
            "qualifications": (Qualification(field="hundreds", minimum=1),),
            "orderby": "hundreds",
            "orderbyad": "reverse",
            "size": 200,
        }
    )
    return {
        results_url(query): result_page(
            [batting_row(1, "Alpha", "AAA", "2010-2020", 10, 1000, "50.00", 1000, "100.00", 1, 1)],
            pages=2,
            total=201,
        )
    }


def pages_for(scenario: str) -> dict[str, FetchResponse | str]:
    scenarios: dict[str, Any] = {
        "happy": happy_pages,
        "audit": happy_pages,
        "block": block_pages,
        "retry-after": retry_after_pages,
        "broad": broad_pages,
    }
    try:
        return scenarios[scenario]()
    except KeyError as error:
        raise ValueError(f"unknown e2e scenario {scenario!r}") from error
