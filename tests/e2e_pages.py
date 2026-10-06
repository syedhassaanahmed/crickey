from __future__ import annotations

# ruff: noqa: E501
from datetime import date
from typing import Any

from crickey.fetcher import FetchResponse
from crickey.query import PlayerPageSpec, Qualification, ResolvedPeriod, StatsguruQuery

AS_OF = date(2026, 10, 4)
RECENT_MATCH_LINE = (
    "Freshness: newest match Statsguru included is Fiction XI v Sampleland XI at Novel Park, "
    "1st ODI (ODI # 900001), ending 3 Oct 2026."
)

PRIMARY_NAME = "Nira Vale"
AMBIGUOUS_NAME = "Nira"
SECOND_CANDIDATE = "Nira Quill"
PRIMARY_ID = 910001
SECOND_ID = 910002
LEADER_ID = 910101
TARGET_BEATER_ID = 910201
TARGET_LEVEL_ID = 910202
BOWLER_ID = 910301


def player_profile_url(player_id: int) -> str:
    return f"https://stats.cricinfo.com/ci/content/player/{player_id}.html"


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
        f"{label} player</a> ({span}, 24 matches)</td></tr>"
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
    <tr class="data1"><td><a href="/ci/content/player/{player_id}.html">{name}</a> ({team})</td><td>{span}</td><td>12</td><td>{inns}</td><td>1</td><td>{runs}</td><td>124*</td><td>{ave}</td><td>{bf}</td><td>{sr}</td><td>{hundreds}</td><td>{fifties}</td><td>0</td></tr>
    """


def recent_matches_table() -> str:
    return """
    <table class="engineTable"><tr class="data2"><td><b>Statsguru includes the following current or recent ODIs:</b></td></tr>
    <tr class="data2"><td>Fiction XI v Sampleland XI at Novel Park, 1st ODI, Oct 3, 2026 [<a href="/ci/engine/match/900001.html">ODI # 900001</a>]</td></tr>
    </table>
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
    {recent_matches_table()}
    </body></html>
    """


def bowling_page() -> str:
    return f"""
    <html><body>
    <table class="engineTable"><caption>Overall figures</caption>
    <tr><th>Player</th><th>Span</th><th>Mat</th><th>Overs</th><th>Wkts</th></tr>
    <tr class="data1"><td><a href="/ci/content/player/{BOWLER_ID}.html">Taro Moss</a> (QQQ)</td><td>2021-2026</td><td>31</td><td>126.4</td><td>64</td></tr>
    </table>
    <table><tr><td>Page <b>1</b> of <b>1</b></td><td>Showing <b>1</b> - <b>1</b> of <b>1</b></td></tr></table>
    {recent_matches_table()}
    </body></html>
    """


def player_page(row_html: str) -> str:
    return f"""
    <html><body>
    <table class="engineTable"><caption>Career averages</caption>
    <tr><th></th><th>Span</th><th>Mat</th><th>Inns</th><th>NO</th><th>Runs</th><th>HS</th><th>Ave</th><th>BF</th><th>SR</th><th>100</th><th>50</th><th>0</th></tr>
    {row_html}
    </table>
    {recent_matches_table()}
    </body></html>
    """


def primary_search_rows() -> tuple[str, ...]:
    return (
        player_search_row(PRIMARY_NAME, "QQQ", PRIMARY_ID, 1, "Test matches", "2018 - 2026"),
        player_search_row(
            PRIMARY_NAME, "QQQ", PRIMARY_ID, 2, "One-Day Internationals", "2017 - 2026"
        ),
        player_search_row(
            PRIMARY_NAME, "QQQ", PRIMARY_ID, 3, "Twenty20 Internationals", "2019 - 2026"
        ),
    )


def leaderboard_query(minimum: int = 10) -> StatsguruQuery:
    return StatsguruQuery(
        **{
            "class": 2,
            "type": "batting",
            "qualifications": (Qualification(field="hundreds", minimum=minimum),),
            "orderby": "hundreds",
            "size": 200,
        }
    )


def comparison_queries() -> tuple[StatsguruQuery, StatsguruQuery]:
    period = ResolvedPeriod(start="2019-03-04", end="2026-08-19")
    base = StatsguruQuery(
        **{
            "class": 3,
            "type": "batting",
            "period": period,
            "qualifications": (Qualification(field="runs", minimum=1000),),
            "orderby": "batting_average",
            "size": 200,
        }
    )
    proof = base.model_copy(
        update={
            "qualifications": (
                Qualification(field="runs", minimum=1000),
                Qualification(field="batting_average", minimum="34.56"),
                Qualification(field="batting_strike_rate", minimum="123.45"),
            )
        }
    )
    return base, proof


def player_record_spec() -> PlayerPageSpec:
    return PlayerPageSpec(player_id=PRIMARY_ID, **{"class": 2, "type": "batting", "trophy": 12})


def query_stats_query() -> StatsguruQuery:
    return StatsguruQuery(
        **{
            "class": 2,
            "type": "bowling",
            "qualifications": (Qualification(field="wickets", minimum=1),),
            "orderby": "wickets",
            "orderbyad": "reverse",
        }
    )


LEADERBOARD_PROOF_URL = results_url(leaderboard_query(10))
BASE_COMPARISON_URL, COMPARISON_PROOF_QUERY = comparison_queries()
COMPARISON_PROOF_URL = results_url(COMPARISON_PROOF_QUERY)
PLAYER_RECORD_PROOF_URL = player_record_spec().url(as_of=AS_OF)
QUERY_STATS_URL = results_url(query_stats_query())
BROAD_URL = results_url(leaderboard_query(1))
RETRY_AFTER_PAST_URL = results_url(leaderboard_query(10))
RETRY_AFTER_SUCCESS_URL = results_url(leaderboard_query(11))


def happy_pages() -> dict[str, FetchResponse | str | list[FetchResponse | str]]:
    pages: dict[str, FetchResponse | str | list[FetchResponse | str]] = {
        player_search_url(PRIMARY_NAME): player_search_page(*primary_search_rows()),
        player_search_url(AMBIGUOUS_NAME): player_search_page(
            *primary_search_rows(),
            player_search_row(
                SECOND_CANDIDATE, "RRR", SECOND_ID, 3, "Twenty20 Internationals", "2020 - 2026"
            ),
        ),
        "https://stats.cricinfo.com/ci/engine/stats/index.html?class=1;filter=advanced;type=batting": """
        <form name="gurumenu">
        <select name="ground">
        <option value="701">QQQ: North Novel Ground</option>
        <option value="702">QQQ: South Novel Ground</option>
        </select>
        </form>
        """,
    }
    pages[LEADERBOARD_PROOF_URL] = result_page(
        [
            batting_row(
                LEADER_ID,
                "Aster Finch",
                "AAA",
                "2020-2026",
                77,
                3773,
                "49.00",
                4211,
                "89.59",
                11,
                18,
            ),
            batting_row(
                PRIMARY_ID,
                PRIMARY_NAME,
                "QQQ",
                "2017-2026",
                144,
                4977,
                "34.56",
                4032,
                "123.45",
                12,
                22,
            ),
        ],
        total=2,
    )
    base_query, proof_query = comparison_queries()
    comparison_rows = [
        batting_row(
            TARGET_BEATER_ID,
            "Cedar Rune",
            "SSS",
            "2021-2026",
            41,
            1567,
            "45.10",
            1007,
            "155.61",
            2,
            9,
        ),
        batting_row(
            TARGET_LEVEL_ID, "Mira Sol", "TTT", "2020-2025", 39, 1188, "36.00", 840, "141.42", 1, 6
        ),
        batting_row(
            PRIMARY_ID, PRIMARY_NAME, "QQQ", "2019-2026", 92, 2789, "34.56", 2259, "123.45", 3, 17
        ),
    ]
    form_url, form = career_form(PRIMARY_ID, 3, "04 Mar 2019", "19 Aug 2026")
    pages[form_url] = form
    pages[results_url(base_query)] = result_page(comparison_rows, total=3)
    pages[results_url(proof_query)] = result_page(comparison_rows, total=3)
    pages[PLAYER_RECORD_PROOF_URL] = player_page(
        '<tr class="data1"><td>unfiltered</td><td>2017-2026</td><td>64</td><td>61</td><td>5</td><td>2574</td><td>143*</td><td>45.96</td><td>2801</td><td>91.89</td><td>9</td><td>12</td><td>1</td></tr>'
        '<tr class="data1"><td>filtered</td><td>2023-2026</td><td>9</td><td>9</td><td>1</td><td>477</td><td>112*</td><td>59.62</td><td>501</td><td>95.20</td><td>2</td><td>2</td><td>0</td></tr>'
    )
    pages[QUERY_STATS_URL] = bowling_page()
    pages[BROAD_URL] = result_page(
        [
            batting_row(
                910401, "Wide Query", "WWW", "2022-2026", 10, 501, "55.66", 411, "121.89", 1, 2
            )
        ],
        pages=5,
        total=801,
    )
    return pages


def block_pages() -> dict[str, FetchResponse | str | list[FetchResponse | str]]:
    pages = {player_search_url(PRIMARY_NAME): player_search_page(*primary_search_rows())}
    url = LEADERBOARD_PROOF_URL
    pages[url] = FetchResponse(url=url, status_code=403, headers={}, text="<html>Statsguru</html>")
    return pages


def retry_after_pages() -> dict[str, FetchResponse | str | list[FetchResponse | str]]:
    success_page = result_page(
        [
            batting_row(
                910501, "Retry Winner", "YYY", "2022-2026", 80, 3000, "40.00", 3100, "96.77", 11, 10
            )
        ],
        total=1,
    )
    return {
        RETRY_AFTER_SUCCESS_URL: [
            FetchResponse(
                url=RETRY_AFTER_SUCCESS_URL,
                status_code=429,
                headers={"Retry-After": "30"},
                text="<html>Statsguru</html>",
            ),
            success_page,
        ],
        RETRY_AFTER_PAST_URL: FetchResponse(
            url=RETRY_AFTER_PAST_URL,
            status_code=429,
            headers={"Retry-After": "9999"},
            text="<html>Statsguru</html>",
        ),
    }


def pages_for(scenario: str) -> dict[str, FetchResponse | str | list[FetchResponse | str]]:
    scenarios: dict[str, Any] = {
        "happy": happy_pages,
        "audit": happy_pages,
        "block": block_pages,
        "retry-after": retry_after_pages,
    }
    try:
        return scenarios[scenario]()
    except KeyError as error:
        raise ValueError(f"unknown e2e scenario {scenario!r}") from error
