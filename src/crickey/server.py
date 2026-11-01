from __future__ import annotations

import functools
import re
from collections.abc import Awaitable, Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass, is_dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from math import ceil
from typing import Annotated, Any, get_args

import pandas as pd
from mcp.server import MCPServer
from mcp.server.mcpserver.context import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field, PositiveInt, ValidationError

from crickey import id_tables
from crickey.fetcher import (
    Fetcher,
    FetcherError,
    Freshness,
    TooBroadError,
    UnavailableUrlError,
    freshness_from_end_date,
    request_tally,
    start_request_tally,
)
from crickey.ids import LookupResult, lookup_continent, lookup_host
from crickey.metrics import (
    FIELDING_METRICS,
    BetterDirection,
    DefaultMinimum,
    Metric,
    batting_metric,
    bowling_metric,
    fielding_metric,
    rank_key,
)
from crickey.parsers import (
    GROUPING_COLUMNS,
    Overs,
    PlayerFormat,
    PlayerPageNoRecordsError,
    RecentMatch,
    ResultsPage,
    Span,
    StatsguruParseError,
    parse_current_or_recent_matches,
    parse_grouped_rows,
    parse_player_page,
    parse_results_page,
)
from crickey.proof import ProofLink, Threshold, build_proof_link
from crickey.query import (
    CLASS_LABELS,
    SINGLE_VALUE_LIST_FIELDS,
    Period,
    PlayerPageSpec,
    Qualification,
    QuerySpecError,
    ResolvedPeriod,
    SeasonPeriod,
    StatsguruQuery,
    StatType,
    SymbolicPeriod,
    SymbolicPeriodKind,
    player_search_url,
)
from crickey.render import (
    AnswerRenderInput,
    RenderedTable,
    RenderPlayer,
    freshness_line,
    render_answer,
)
from crickey.resolve import NameResolver, PeriodResolver, PlayerCandidate, names_agree
from crickey.resolve import _country_codes as country_codes
from crickey.resolve import _normalize as normalize_country
from crickey.resolve import _player_resolution_from_html as player_resolution_from_html
from crickey.settings import Settings

SERVER_INSTRUCTIONS = """\
Prefer crickey's answer tools.
Show each answer tool's answer as it is, or keep at least its Answer proof or Input/method link.
Do not do multi-row arithmetic yourself; use crickey tools for comparisons and derived rates.
To compare named players, call player_record once for each; better_than_player finds who beat one.
Cite only links that came from crickey tools, as full URLs copied exactly.
Read "T20" as T20I unless a domestic or franchise league is named."""

# Every tool result's `_meta` says what the call cost Statsguru, for the eval's cost check. Models
# don't see it.
STATSGURU_META_KEY = "crickey/statsguru"

FETCH_BUDGET_SECONDS = 240.0
FIND_PLAYER_BUDGET_SECONDS = 60.0
MAX_QUERY_STATS_LIMIT = 200
DEFAULT_QUERY_STATS_LIMIT = 50

_READ_ONLY_ANNOTATIONS = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
_QUERY_STATS_SINGLE_VALUE_LIST_FIELDS = (
    ", ".join(SINGLE_VALUE_LIST_FIELDS[:-1]) + f" and {SINGLE_VALUE_LIST_FIELDS[-1]}"
)
# Some renderers hide the query's fields, so its description names the two it needs (#85).
_STAT_TYPES = get_args(StatType)
_QUERY_STATS_QUERY = (
    "The Statsguru query. It needs class (1 Test, 2 ODI, 3 T20I, 6 all T20 or 11 all formats) "
    f"and type ({', '.join(_STAT_TYPES[:-1])} or {_STAT_TYPES[-1]}); filters and sort are "
    "optional."
)
_FORMAT_CLASSES = {
    "test": 1,
    "tests": 1,
    "odi": 2,
    "odis": 2,
    "one-day international": 2,
    "one day international": 2,
    "t20i": 3,
    "t20is": 3,
    "twenty20 international": 3,
    "twenty20 internationals": 3,
    "t20": 3,
    "all t20": 6,
    "twenty20": 6,
    "all internationals": 11,
    "all formats": 11,
    "test/odi/t20i": 11,
    "combined internationals": 11,
}
_DEFAULT_FIND_CLASSES = (1, 2, 3, 6, 11)
_METADATA_COLUMNS = {
    "player_name",
    "player_id",
    "player_team_codes",
    "match_id",
    "series_id",
    "series_page_id",
}
_BOWLING_RATE_METRICS = {"bowling_average", "economy_rate", "bowling_strike_rate"}
_FILTERED_BOWLING_RATE_MINIMUMS = {1: 30, 2: 30, 3: 20, 6: 50, 11: 50}
_FIELDING_RATE_METRICS = {"dismissals_per_innings"}
_FILTERED_FIELDING_MINIMUMS = {1: 20, 2: 20, 3: 20, 6: 50, 11: 30}
_COUNT_LEADERBOARD_MINIMUM = 1
_ANSWER_DISCIPLINES = ("batting", "bowling", "fielding")
_RECORD_DISCIPLINES = ("batting", "bowling")
# Answers name what Statsguru's fielding columns leave out or fold in.
_FIELDING_NOTE = "Statsguru's fielding figures count catches and stumpings, not run-outs."
_DISMISSALS_NOTE = "Dismissals are catches plus stumpings, including those taken as a wicketkeeper."
_FIELDING_METRIC_NOTES = {
    "catches": (
        "Catches include catches taken as a wicketkeeper; catches as a fielder leave those out."
    ),
    "dismissals": _DISMISSALS_NOTE,
    "dismissals_per_innings": _DISMISSALS_NOTE,
}


class AnswerMetric(StrEnum):
    RUNS = "runs"
    AVERAGE = "average"
    STRIKE_RATE = "strike_rate"
    HUNDREDS = "hundreds"
    FIFTIES = "fifties"
    INNINGS_PER_HUNDRED = "innings_per_hundred"
    INNINGS_PER_FIFTY_PLUS = "innings_per_fifty_plus"
    BALLS_PER_DISMISSAL = "balls_per_dismissal"
    WICKETS = "wickets"
    BOWLING_AVERAGE = "bowling_average"
    ECONOMY_RATE = "economy_rate"
    BOWLING_STRIKE_RATE = "bowling_strike_rate"
    FIVE_WICKETS = "five_wickets"
    TEN_WICKETS = "ten_wickets"
    CATCHES = "catches"
    FIELDER_CATCHES = "fielder_catches"
    KEEPER_CATCHES = "keeper_catches"
    STUMPINGS = "stumpings"
    DISMISSALS = "dismissals"
    DISMISSALS_PER_INNINGS = "dismissals_per_innings"


class AnswerDiscipline(StrEnum):
    BATTING = "batting"
    BOWLING = "bowling"
    FIELDING = "fielding"


class RecordDiscipline(StrEnum):
    BATTING = "batting"
    BOWLING = "bowling"


class SplitBy(StrEnum):
    HOST = "host"
    OPPOSITION = "opposition"
    YEAR = "year"
    CONTINENT = "continent"


@dataclass(frozen=True)
class _Split:
    noun: str
    plural: str
    preposition: str
    title: str
    filter_hint: str


@dataclass(frozen=True)
class _SplitCount:
    column: str
    singular: str
    plural: str
    key: str


# How answers name each grouping of the summary page's "Career summary" table.
_SPLITS = {
    "host": _Split("host country", "host countries", "in", "Host country", "host_country"),
    "opposition": _Split("opposition", "oppositions", "against", "Opposition", "opposition"),
    "year": _Split("year", "years", "in", "Year", "period"),
    "continent": _Split("continent", "continents", "in", "Continent", "continent"),
}
_SPLIT_ALIASES = {"host_country": "host", "opponent": "opposition"}
# Columns each split's table shows, in Statsguru's order, and the column whose groups it counts.
_SPLIT_COLUMNS = {
    "batting": ("Mat", "Inns", "Runs", "Ave", "SR", "100", "50"),
    "bowling": ("Mat", "Wkts", "Ave", "Econ", "SR", "5"),
}
_SPLIT_COUNTS = {
    "batting": _SplitCount("100", "hundred", "hundreds", "hundreds"),
    "bowling": _SplitCount("5", "five-wicket haul", "five-wicket hauls", "five_wicket_hauls"),
}

# What each tool parameter takes, in one line, so small models put a question's details in the
# right parameters (#66). Defaults and allowed values are in the schemas already.
_TOURNAMENTS = "A tournament goes in trophy."
FormatParam = Annotated[
    str | int, Field(description='Test, ODI, T20I, "all T20" or "all formats".')
]
AnswerDisciplineParam = Annotated[
    AnswerDiscipline | str,
    Field(description="Batting, bowling or fielding (catches and stumpings)."),
]
RecordDisciplineParam = Annotated[RecordDiscipline | str, Field(description="Batting or bowling.")]
PlayerNameParam = Annotated[
    str | None, Field(description="The player's name. Give this or player_id.")
]
PlayerIdParam = Annotated[
    PositiveInt | None,
    Field(description="Player ID from find_player or a clarification. Give this or player_name."),
]
# A period is plain text in one of a few forms, since some models' tool renderers hide an object's
# fields. The descriptions and errors show the forms.
_PERIOD_RANGES = (
    'years ("2010 to 2019"), dates ("2010-01-01 to 2019-12-31") or a season ("season 2011/12")'
)
_PLAYER_SPANS = '"first N years", "last N years"'
# No number in the example: small models copied it instead of the question's.
_PLAYER_SPANS_NOTE = "N is the number of years."
LeaderboardPeriodParam = Annotated[
    str | None,
    Field(description=f"All time by default; or {_PERIOD_RANGES}. {_TOURNAMENTS}"),
]
ComparisonPeriodParam = Annotated[
    str | None,
    Field(
        description=(
            'For the span the player played, leave it out or give "career": crickey reads its '
            'dates from Statsguru. Otherwise "all time", '
            f"{_PLAYER_SPANS}, {_PERIOD_RANGES}. {_PLAYER_SPANS_NOTE} {_TOURNAMENTS}"
        )
    ),
]
RecordPeriodParam = Annotated[
    str | None,
    Field(
        description=(
            f"The whole record by default; or {_PLAYER_SPANS}, {_PERIOD_RANGES}. "
            f"{_PLAYER_SPANS_NOTE} {_TOURNAMENTS}"
        )
    ),
]
TeamParam = Annotated[
    str | list[str] | None, Field(description='The players\' team, e.g. "New Zealand".')
]
OppositionParam = Annotated[
    str | list[str] | None, Field(description='The opposing team, e.g. "Australia".')
]
HostCountryParam = Annotated[
    str | list[str] | None, Field(description='Country the matches were in, e.g. "England".')
]
ContinentParam = Annotated[
    str | list[str] | None, Field(description='Continent the matches were in, e.g. "Europe".')
]
GroundParam = Annotated[str | list[str] | None, Field(description='A ground, e.g. "Lord\'s".')]
TrophyParam = Annotated[
    str | list[str] | None, Field(description='A tournament or trophy, e.g. "The Ashes".')
]
HomeOrAwayParam = Annotated[str | int | None, Field(description="home, away or neutral.")]
MatchResultParam = Annotated[
    str | int | None, Field(description="won, lost, tied, drawn or no result.")
]
SeriesTeamParam = Annotated[
    str | list[str] | None,
    Field(description='A team that played in the series, e.g. "New Zealand".'),
]
# Text rather than an enum, so plain words such as "three or more" work; the description lists
# the values crickey reads.
NumberOfTeamsParam = Annotated[
    str | int | None, Field(description='Teams in each series: "2", "3+", "3-4" or "5+".')
]
# Statsguru's tournament types: 2 two-team series, 3 three- or four-team tournaments, 5 five or
# more teams. It can't tell three teams from four.
_SERIES_SIZES = {"2": (2,), "3+": (3, 5), "3-4": (3,), "5+": (5,)}
_SERIES_SIZE_SYNONYMS = {
    "two": "2",
    "3 or more": "3+",
    "three or more": "3+",
    "at least 3": "3+",
    "more than 2": "3+",
    "3 or 4": "3-4",
    "three or four": "3-4",
    "5 or more": "5+",
    "five or more": "5+",
    "at least 5": "5+",
}


def create_server(
    settings: Settings | None = None,
    *,
    fetcher: Fetcher | None = None,
    clock=None,
) -> MCPServer:
    settings = Settings() if settings is None else settings
    fetcher = Fetcher(settings, clock=clock) if fetcher is None else fetcher
    mcp = MCPServer("crickey", instructions=SERVER_INSTRUCTIONS)

    @mcp.tool(
        annotations=_READ_ONLY_ANNOTATIONS,
        description=(
            "Example: Which Babar played ODIs? Find Statsguru player candidates "
            "by name, with player ID, country, formats and career spans."
        ),
    )
    @_reports_statsguru_cost
    async def find_player(
        name: Annotated[str, Field(description='A name or part of one, e.g. "Babar".')],
        format: Annotated[
            str | int | None, Field(description="Only candidates who played this format.")
        ] = None,
        country: Annotated[
            str | None, Field(description='Only candidates from this country, e.g. "England".')
        ] = None,
        ctx: Context | None = None,
    ) -> CallToolResult:
        if not name.strip():
            raise ToolError("name is required.")
        class_ids = (_format_class(format),) if format is not None else _DEFAULT_FIND_CLASSES
        candidates: dict[int, PlayerCandidate] = {}
        country_filter_applied = country is None
        progress = _progress_callback(ctx)
        try:
            async with fetcher.call(budget=FIND_PLAYER_BUDGET_SECONDS, progress=progress) as call:
                url = player_search_url(name)
                was_cached = fetcher.cache.get(url, fetcher.clock.monotonic()) is not None
                html = await call.fetch(url, freshness=Freshness.LOOKUP)
                candidates, country_filter_applied = _player_candidates_from_search(
                    name, html, class_ids, country
                )
                if not candidates and was_cached:
                    html = await call.fetch(url, freshness=Freshness.LOOKUP, force_refetch=True)
                    candidates, country_filter_applied = _player_candidates_from_search(
                        name, html, class_ids, country
                    )
        except (FetcherError, StatsguruParseError) as error:
            raise ToolError(str(error)) from error

        ordered = tuple(candidates.values())
        exact = [
            candidate
            for candidate in ordered
            if name.casefold().strip()
            in {candidate.name.casefold(), (candidate.full_name or "").casefold()}
        ]
        status = (
            "match"
            if len(ordered) == 1 and country_filter_applied and (exact or country or format)
            else "needs_clarification"
        )
        if not ordered:
            status = "not_found"
        payload = {
            "status": status,
            "query": name,
            "candidates": [_candidate_payload(candidate) for candidate in ordered],
        }
        if country is not None and not country_filter_applied:
            payload["note"] = (
                f"No candidate matched country {country!r}; showing unfiltered candidates."
            )
        if status == "match":
            payload["match"] = payload["candidates"][0]
        return _tool_result(_find_player_summary(payload), payload)

    @mcp.tool(
        annotations=_READ_ONLY_ANNOTATIONS,
        description=(
            "Example: Who has the most ODI wickets against Australia? Compile and optionally "
            "fetch any Statsguru query, returning rows, totals and the pinned link. "
            "Lists work for every filter except "
            f"{_QUERY_STATS_SINGLE_VALUE_LIST_FIELDS}."
        ),
    )
    @_reports_statsguru_cost
    async def query_stats(
        query: Annotated[StatsguruQuery, Field(description=_QUERY_STATS_QUERY)],
        limit: Annotated[
            int, Field(description=f"Rows to return, up to {MAX_QUERY_STATS_LIMIT}.")
        ] = DEFAULT_QUERY_STATS_LIMIT,
        fetch: Annotated[
            bool, Field(description="False builds the pinned link without fetching.")
        ] = True,
        ctx: Context | None = None,
    ) -> CallToolResult:
        if limit < 1 or limit > MAX_QUERY_STATS_LIMIT:
            raise ToolError(f"limit must be from 1 to {MAX_QUERY_STATS_LIMIT}.")
        try:
            stats_query = query
            as_of = _today(fetcher)
            fetch_query = _query_for_limit(stats_query, limit)
            url = fetch_query.results_url(as_of=as_of)
            label = fetch_query.label(as_of=as_of)
        except (ValidationError, QuerySpecError, ValueError) as error:
            raise ToolError(_validation_message(error)) from error

        payload: dict[str, Any] = {"link": url, "label": label, "fetch": fetch}
        if not fetch:
            payload.update({"columns": [], "rows": [], "total": None, "freshness": None})
            return _tool_result(f"Built Statsguru link without fetching: {label}", payload)

        progress = _progress_callback(ctx)
        try:
            async with fetcher.call(budget=FETCH_BUDGET_SECONDS, progress=progress) as call:
                parsed_pages = await _fetch_limited_pages(
                    call,
                    fetch_query,
                    limit=limit,
                    as_of=as_of,
                    freshness=_freshness_for_query(fetch_query, as_of),
                    max_pages=fetcher.settings.max_pages,
                )
        except FetcherError as error:
            raise ToolError(str(error)) from error
        except StatsguruParseError as error:
            raise ToolError(str(error)) from error

        first_page = parsed_pages[0]
        columns = _display_columns(first_page)
        if first_page.no_records:
            freshness = freshness_line(first_page.current_or_recent_matches, today=as_of)
            payload.update(
                {
                    "columns": list(columns),
                    "rows": [],
                    "total": 0,
                    "page_count": first_page.totals.pages,
                    "freshness": freshness,
                }
            )
            return _tool_result(f"No records found for {label}. {freshness}", payload)
        rows = [
            _display_row(row, columns)
            for page in parsed_pages
            for row in page.table.to_dict("records")
        ][:limit]
        freshness = freshness_line(first_page.current_or_recent_matches, today=as_of)
        payload.update(
            {
                "columns": list(columns),
                "rows": rows,
                "total": first_page.totals.total or len(rows),
                "page_count": first_page.totals.pages,
                "freshness": freshness,
            }
        )
        return _tool_result(
            f"Fetched {len(rows)} row(s) of {payload['total']} for {label}. {freshness}",
            payload,
            text_required_columns=_text_required_columns(fetch_query),
        )

    @mcp.tool(
        annotations=_READ_ONLY_ANNOTATIONS,
        description=(
            "Example: Who has the best ODI batting average, with at least 50 innings? Who "
            "scored T20I hundreds most often, with at least 3 hundreds? Bowling example: Which "
            "bowlers have the best Test economy rate in Australia? Fielding example: Which "
            "wicketkeepers have made the most ODI stumpings? Return a batting, bowling or "
            "fielding leaderboard with proof links."
        ),
    )
    @_reports_statsguru_cost
    async def leaderboard(
        format: FormatParam,
        metric: Annotated[
            AnswerMetric | str,
            Field(description='What to rank by, e.g. "innings_per_hundred" or "wickets".'),
        ],
        discipline: AnswerDisciplineParam = AnswerDiscipline.BATTING,
        period: LeaderboardPeriodParam = None,
        team: TeamParam = None,
        opposition: OppositionParam = None,
        host_country: HostCountryParam = None,
        continent: ContinentParam = None,
        ground: GroundParam = None,
        trophy: TrophyParam = None,
        home_or_away: HomeOrAwayParam = None,
        match_result: MatchResultParam = None,
        minimum: Annotated[
            int | Decimal | None,
            Field(description='Qualifying minimum, e.g. 10 for "minimum 10 centuries".'),
        ] = None,
        top_n: Annotated[int, Field(description="How many players to list, up to 200.")] = 10,
        ctx: Context | None = None,
    ) -> CallToolResult:
        return await _leaderboard_tool(
            fetcher,
            fetcher.settings,
            format=format,
            discipline=discipline,
            metric_key=metric,
            period=period,
            team=team,
            opposition=opposition,
            host_country=host_country,
            continent=continent,
            ground=ground,
            trophy=trophy,
            home_or_away=home_or_away,
            match_result=match_result,
            minimum=minimum,
            top_n=top_n,
            ctx=ctx,
        )

    @mcp.tool(
        annotations=_READ_ONLY_ANNOTATIONS,
        description=(
            "Example: Who has reached ODI fifties more often than Kane Williamson? Who beat "
            "Babar Azam's T20I average and strike rate during his career? Bowling example: "
            "Which T20I bowlers have a better economy rate than Rashid Khan? Fielding "
            "example: Which fielders have taken more ODI catches than Ravindra Jadeja? "
            "Find the players who beat one player. To see which of two or more named players "
            "did better, call player_record once for each instead. Name the player with "
            "player_name, or with player_id from find_player or a clarification when names clash."
        ),
    )
    @_reports_statsguru_cost
    async def better_than_player(
        *,
        player_name: PlayerNameParam = None,
        player_id: PlayerIdParam = None,
        format: FormatParam,
        metrics: Annotated[
            list[AnswerMetric | str],
            Field(description='1 to 3 metrics to compare, e.g. ["average", "strike_rate"].'),
        ],
        discipline: AnswerDisciplineParam = AnswerDiscipline.BATTING,
        match: Annotated[
            str, Field(description='"all": better on every metric; "any": on at least one.')
        ] = "all",
        period: ComparisonPeriodParam = None,
        team: TeamParam = None,
        opposition: OppositionParam = None,
        host_country: HostCountryParam = None,
        continent: ContinentParam = None,
        ground: GroundParam = None,
        trophy: TrophyParam = None,
        home_or_away: HomeOrAwayParam = None,
        match_result: MatchResultParam = None,
        minimum: Annotated[
            int | Decimal | None, Field(description="Qualifying minimum for the other players.")
        ] = None,
        ctx: Context | None = None,
    ) -> CallToolResult:
        return await _better_than_player_tool(
            fetcher,
            fetcher.settings,
            player_name=player_name,
            player_id=player_id,
            format=format,
            discipline=discipline,
            metric_keys=metrics,
            match_mode=match,
            period=period,
            team=team,
            opposition=opposition,
            host_country=host_country,
            continent=continent,
            ground=ground,
            trophy=trophy,
            home_or_away=home_or_away,
            match_result=match_result,
            minimum=minimum,
            ctx=ctx,
        )

    @mcp.tool(
        annotations=_READ_ONLY_ANNOTATIONS,
        description=(
            "Example: What was Steve Smith's ODI strike rate in his first 5 years? How many "
            "runs has Joe Root scored in the Ashes? Bowling examples: Who bowled better in "
            "England, Trent Boult or Mitchell Starc? Compare the T20I bowling of Jasprit "
            "Bumrah, Shaheen Shah Afridi and Kagiso Rabada against Australia. Split example: "
            "In which countries has Babar Azam not made an ODI hundred? "
            "Return one player's batting or bowling record with a proof link; split_by "
            "(host, opposition, year or continent) splits it into Statsguru's grouped rows "
            "from the same page and counts the groups with a hundred, or a five-wicket haul "
            "for bowling. One player per call: compare players with one call each, and a "
            "question about all players needs player-by-player checks. Name the player with "
            "player_name, or with player_id from find_player or a clarification when names "
            "clash."
        ),
    )
    @_reports_statsguru_cost
    async def player_record(
        *,
        player_name: PlayerNameParam = None,
        player_id: PlayerIdParam = None,
        format: FormatParam,
        discipline: RecordDisciplineParam = RecordDiscipline.BATTING,
        period: RecordPeriodParam = None,
        opposition: OppositionParam = None,
        host_country: HostCountryParam = None,
        continent: ContinentParam = None,
        ground: GroundParam = None,
        trophy: TrophyParam = None,
        home_or_away: HomeOrAwayParam = None,
        match_result: MatchResultParam = None,
        split_by: Annotated[
            SplitBy | str | None, Field(description="Split the record into one row per group.")
        ] = None,
        ctx: Context | None = None,
    ) -> CallToolResult:
        return await _player_record_tool(
            fetcher,
            player_name=player_name,
            player_id=player_id,
            format=format,
            discipline=discipline,
            period=period,
            opposition=opposition,
            host_country=host_country,
            continent=continent,
            ground=ground,
            trophy=trophy,
            home_or_away=home_or_away,
            match_result=match_result,
            split_by=split_by,
            ctx=ctx,
        )

    @mcp.tool(
        annotations=_READ_ONLY_ANNOTATIONS,
        description=(
            "Example: How many T20I tournaments of five or more teams have been held in the "
            "UAE? Team example: How many Test series has New Zealand played in England? "
            "Count the series in a format and list them, with a proof link. The country the "
            "series were played in goes in host_country, and a team that played in them in "
            "team. Also filter by the number of teams in each series, a tournament and a period."
        ),
    )
    @_reports_statsguru_cost
    async def series_count(
        format: FormatParam,
        host_country: HostCountryParam = None,
        team: SeriesTeamParam = None,
        number_of_teams: NumberOfTeamsParam = None,
        trophy: TrophyParam = None,
        period: LeaderboardPeriodParam = None,
        ctx: Context | None = None,
    ) -> CallToolResult:
        return await _series_count_tool(
            fetcher,
            format=format,
            host_country=host_country,
            team=team,
            number_of_teams=number_of_teams,
            trophy=trophy,
            period=period,
            ctx=ctx,
        )

    return mcp


async def _leaderboard_tool(
    fetcher: Fetcher,
    settings: Settings,
    *,
    format: str | int,
    discipline: AnswerDiscipline | str,
    metric_key: str,
    period: str | None,
    team: str | list[str] | None,
    opposition: str | list[str] | None,
    host_country: str | list[str] | None,
    continent: str | list[str] | None,
    ground: str | list[str] | None,
    trophy: str | list[str] | None,
    home_or_away: str | int | None,
    match_result: str | int | None,
    minimum: int | Decimal | None,
    top_n: int,
    ctx: Context | None,
) -> CallToolResult:
    if top_n < 1 or top_n > 200:
        raise ToolError("top_n must be from 1 to 200.")
    try:
        class_id = _format_class(format)
        discipline_value = _answer_discipline(discipline)
        metric = _answer_metric(discipline_value, metric_key)
        metric.require_supported(class_id)
        resolved_period = _parse_period(period, without_player="leaderboard ranks every player")
    except (ValueError, QuerySpecError) as error:
        raise ToolError(str(error)) from error
    progress = _progress_callback(ctx)
    try:
        async with fetcher.call(budget=FETCH_BUDGET_SECONDS, progress=progress) as call:
            filters = await _answer_filters(
                fetcher,
                class_id,
                call=call,
                team=team,
                opposition=opposition,
                host_country=host_country,
                continent=continent,
                ground=ground,
                trophy=trophy,
                home_or_away=home_or_away,
                match_result=match_result,
            )
            if filters.get("needs_clarification"):
                return _tool_result("A filter needs clarification.", filters)
            default_minimum = _leaderboard_default_minimum(
                metric, class_id, period=resolved_period, filters=filters["query"]
            )
            minimum_value = minimum if minimum is not None else default_minimum.minimum
            minimum_field = default_minimum.field
            query = StatsguruQuery(
                **{
                    "class": class_id,
                    "type": discipline_value,
                    "period": resolved_period,
                    "qualifications": (Qualification(field=minimum_field, minimum=minimum_value),),
                    "orderby": metric.orderby or minimum_field,
                    "size": 200 if metric.is_derived else _page_size_for_top_n(top_n),
                    **filters["query"],
                }
            )
            pages = await _fetch_leaderboard_pages(
                call, query, fetcher, settings, metric, class_id, top_n
            )
    except (
        FetcherError,
        StatsguruParseError,
        ValidationError,
        QuerySpecError,
        ValueError,
    ) as error:
        raise ToolError(
            _validation_message(error) if isinstance(error, ValidationError) else str(error)
        ) from error

    ranked = _ranked_rows(pages, metric, class_id)
    ranked_with_ranks = _select_ranked_with_ties(ranked, metric, class_id, top_n)
    rows = [row for row, _rank in ranked_with_ranks]
    group_value = _group_value(pages, metric)
    as_of = _today(fetcher)
    proof = await build_proof_link(
        query,
        expected_player_ids=tuple(_row_id(row) for row in rows if _row_id(row) is not None),
        formula=metric.formula_label,
        call=call if metric.qualval else None,
        as_of=as_of,
    )
    payload_rows = [
        _metric_row_payload(row, metric, class_id, rank) for row, rank in ranked_with_ranks
    ]
    floor_columns = () if minimum_field == metric.qualval else (minimum_field,)
    answer = render_answer(
        AnswerRenderInput(
            short_answer=_leaderboard_short_answer(metric, payload_rows, group_value),
            table=RenderedTable(
                headers=("Rank", "Player", metric.label, *floor_columns),
                rows=tuple(
                    (
                        row["rank"],
                        row["player"],
                        row["value"],
                        *(row.get(column) for column in floor_columns),
                    )
                    for row in payload_rows
                ),
            ),
            method=(
                _leaderboard_fetch_method(metric, pages, settings),
                "Calculated derived metrics from totals."
                if metric.is_derived
                else "Used Statsguru displayed values.",
                _direction_method(metric),
                "Equal displayed values count as ties.",
            ),
            assumptions=(
                f"Minimum: {minimum_field} >= {minimum_value}.",
                f"Period: {_period_text(resolved_period, as_of=as_of)}.",
                *_fielding_notes(discipline_value, (metric,)),
            ),
            proof_links=(proof,),
            players=tuple(
                RenderPlayer(row["player"], row["player_id"])
                for row in payload_rows
                if row.get("player_id") is not None
            ),
            as_of=as_of,
            current_or_recent_matches=pages[0].current_or_recent_matches,
        )
    )
    return _tool_result(
        f"Built {metric.label} leaderboard.",
        {
            "status": "ok",
            "answer_markdown": answer,
            "rows": payload_rows,
            "group": {
                "metric": metric.key,
                "value": _jsonable(group_value),
                "formula": metric.formula_label,
            },
            "proof": _jsonable(proof),
            "request_pages": len(pages),
        },
    )


async def _series_count_tool(
    fetcher: Fetcher,
    *,
    format: str | int,
    host_country: str | list[str] | None,
    team: str | list[str] | None,
    number_of_teams: str | int | None,
    trophy: str | list[str] | None,
    period: str | None,
    ctx: Context | None,
) -> CallToolResult:
    try:
        class_id = _format_class(format)
        size = _series_size(number_of_teams)
        resolved_period = _parse_period(period, without_player="series_count counts series")
    except ValueError as error:
        raise ToolError(str(error)) from error
    as_of = _today(fetcher)
    progress = _progress_callback(ctx)
    try:
        async with fetcher.call(budget=FETCH_BUDGET_SECONDS, progress=progress) as call:
            filters = await _answer_filters(
                fetcher, class_id, call=call, team=team, host_country=host_country, trophy=trophy
            )
            if filters.get("needs_clarification"):
                return _tool_result("A filter needs clarification.", filters)
            # One row per series, ordered by start date; the page's total is the count.
            query = StatsguruQuery(
                **{
                    "class": class_id,
                    "type": "aggregate",
                    "view": "series",
                    "period": resolved_period,
                    "size": 200,
                    **({"tournament_type": _SERIES_SIZES[size]} if size else {}),
                    **filters["query"],
                }
            )
            url = query.results_url(as_of=as_of)
            page = parse_results_page(
                await call.fetch(url, freshness=_freshness_for_query(query, as_of))
            )
            rows = [] if page.no_records else page.table.to_dict("records")
            # The view leaves the series' names out, and the form's series list has them.
            names = (
                await NameResolver(fetcher).series_names(
                    (row["series_id"] for row in rows if row.get("series_id") is not None),
                    class_id=class_id,
                    call=call,
                )
                if rows
                else {}
            )
    except (
        FetcherError,
        StatsguruParseError,
        ValidationError,
        QuerySpecError,
        ValueError,
    ) as error:
        raise ToolError(
            _validation_message(error) if isinstance(error, ValidationError) else str(error)
        ) from error

    count = 0 if page.no_records else page.totals.total or len(rows)
    payload_rows = [
        {
            "series": names.get(row.get("series_id")),
            "start_date": row.get("Start Date"),
            "matches": row.get("Mat"),
            "winner": row.get("Winner"),
            "series_id": row.get("series_id"),
            "series_page": _series_url(row.get("series_page_id")),
        }
        for row in rows
    ]
    proof = ProofLink(
        label=f"Confirmed Statsguru results: {query.label(as_of=as_of)}",
        url=url,
        confirmed=True,
        fetched=True,
        row_count=count,
    )
    method = ["Counted the rows of Statsguru's series view, one row per series."]
    if filters["query"] or resolved_period is not None:
        method.append(
            "A series counts when any of its matches meets the filters, and its start date and "
            "matches count only those matches."
        )
    if "host" in filters["query"]:
        method.append("A co-hosted tournament counts in each of its host countries.")
    if size in {"3+", "3-4"}:
        method.append("Statsguru counts three- and four-team tournaments together.")
    if count > len(rows):
        method.append(
            f"Listed the first {len(rows)} of {count} series, by start date; the proof link has "
            "them all."
        )
    answer = render_answer(
        AnswerRenderInput(
            short_answer=_series_short_answer(query, count, size, as_of=as_of),
            # Only the proof is linked: models that cited series pages dropped it.
            table=RenderedTable(
                headers=("Series", "Start date", "Matches", "Winner"),
                rows=tuple(
                    (row["series"], row["start_date"], row["matches"], row["winner"])
                    for row in payload_rows
                ),
            ),
            method=tuple(method),
            assumptions=(f"Period: {_period_text(resolved_period, as_of=as_of)}.",),
            proof_links=(proof,),
            players=(),
            as_of=as_of,
            current_or_recent_matches=page.current_or_recent_matches,
        )
    )
    return _tool_result(
        f"Counted {count} series.",
        {
            "status": "ok",
            "answer_markdown": answer,
            "count": count,
            "series": payload_rows,
            "proof": _jsonable(proof),
            "request_pages": 1,
        },
    )


def _series_size(value: str | int | None) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    text = " ".join(str(value).casefold().removesuffix("teams").removesuffix("team").split())
    text = _SERIES_SIZE_SYNONYMS.get(text, text)
    if text not in _SERIES_SIZES:
        raise ValueError(
            f'number_of_teams {value!r} isn\'t one crickey reads: give "2", "3+" for three or '
            'more, "3-4" or "5+". Statsguru counts three- and four-team tournaments together.'
        )
    return text


def _series_short_answer(
    query: StatsguruQuery, count: int, size: str | None, *, as_of: date
) -> str:
    def names(field: str, table: Mapping[int, Mapping[int, str]]) -> str:
        values = getattr(query, field)
        items = values if isinstance(values, tuple | list) else (values,)
        return " or ".join(table[query.class_][int(item)] for item in items)

    sizes = f" of {size} teams" if size else ""
    team = f" involving {names('team', id_tables.TEAMS)}" if query.team is not None else ""
    trophy = f" in the {names('trophy', id_tables.TROPHIES)}" if query.trophy is not None else ""
    host = f" in {names('host', id_tables.HOSTS)}" if query.host is not None else ""
    verb = "was" if count == 1 else "were"
    period = _period_text(query.period, as_of=as_of)
    return (
        f"{count} {CLASS_LABELS[query.class_]} series{sizes}{team}{trophy} {verb} played"
        f"{host}, {period}."
    )


def _series_url(page_id: int | None) -> str | None:
    if page_id is None:
        return None
    return f"https://stats.cricinfo.com/ci/content/series/{page_id}.html"


async def _better_than_player_tool(
    fetcher: Fetcher,
    settings: Settings,
    *,
    player_name: str | None,
    player_id: int | None,
    format: str | int,
    discipline: AnswerDiscipline | str,
    metric_keys: list[AnswerMetric | str],
    match_mode: str,
    period: str | None,
    team: str | list[str] | None,
    opposition: str | list[str] | None,
    host_country: str | list[str] | None,
    continent: str | list[str] | None,
    ground: str | list[str] | None,
    trophy: str | list[str] | None,
    home_or_away: str | int | None,
    match_result: str | int | None,
    minimum: int | Decimal | None,
    ctx: Context | None,
) -> CallToolResult:
    query_name = _require_player(player_name, player_id)
    if not 1 <= len(metric_keys) <= 3:
        raise ToolError("metrics must contain 1 to 3 metrics.")
    if match_mode not in {"all", "any"}:
        raise ToolError("match must be 'all' or 'any'.")
    try:
        class_id = _format_class(format)
        discipline_value = _answer_discipline(discipline)
        metrics = tuple(_answer_metric(discipline_value, key) for key in metric_keys)
        # Without a period, the comparison covers the player's career span.
        requested_period = _parse_period(period) if _has_text(period) else _CAREER
        for metric in metrics:
            metric.require_supported(class_id)
    except ValueError as error:
        raise ToolError(str(error)) from error
    progress = _progress_callback(ctx)
    by_id = player_id is not None
    page_name: str | None = None
    try:
        async with fetcher.call(budget=FETCH_BUDGET_SECONDS, progress=progress) as call:
            if player_id is not None:
                target_id = player_id
                matched: PlayerCandidate | None = None
            else:
                names = NameResolver(fetcher)
                resolution = await names.resolve_player(query_name, class_id=class_id, call=call)
                if resolution.needs_clarification or resolution.match is None:
                    return _clarification_result(
                        query_name, resolution, class_id=class_id, tool="better_than_player"
                    )
                matched = resolution.match
                target_id = matched.player_id
            # By ID, errors name the player only from Statsguru's pages, never from player_name.
            known_name = matched.name if matched is not None else None
            with _target_page_errors(
                target_id, class_id, discipline_value, name=known_name, by_id=by_id
            ):
                period_value = await _player_period(
                    fetcher, target_id, class_id, requested_period, call
                )
            filters = await _answer_filters(
                fetcher,
                class_id,
                call=call,
                team=team,
                opposition=opposition,
                host_country=host_country,
                continent=continent,
                ground=ground,
                trophy=trophy,
                home_or_away=home_or_away,
                match_result=match_result,
            )
            if filters.get("needs_clarification"):
                return _tool_result("A filter needs clarification.", filters)
            min_field, default_floor = _comparison_default_minimum(
                metrics, class_id, period=period_value, filters=filters["query"]
            )

            async def target_floor() -> Decimal | None:
                nonlocal page_name
                with _target_page_errors(
                    target_id, class_id, discipline_value, name=known_name, by_id=by_id
                ):
                    value, page_name = await _target_filtered_floor(
                        call,
                        target_id,
                        class_id=class_id,
                        discipline=discipline_value,
                        period=period_value,
                        filters=filters["player_page"],
                        field=min_field,
                        fetcher=fetcher,
                    )
                return value

            def target_label(*, start: bool = False) -> str:
                if known_name is not None:
                    return known_name
                if page_name is not None:
                    return f"{page_name} (player ID {target_id})"
                return f"{'Player' if start else 'player'} ID {target_id}"

            # By ID, X's name is unknown until X's row or page is read, so name the format.
            rows_text = (
                f"the qualifying Statsguru {CLASS_LABELS[class_id]} rows"
                if by_id
                else "the qualifying Statsguru rows"
            )

            def comparison_query(floor: int | Decimal) -> StatsguruQuery:
                return StatsguruQuery(
                    **{
                        "class": class_id,
                        "type": discipline_value,
                        "period": period_value,
                        "qualifications": (Qualification(field=min_field, minimum=floor),),
                        "orderby": metrics[0].orderby,
                        "orderbyad": "",
                        "size": 200,
                        **filters["query"],
                    }
                )

            floor_value: int | Decimal = minimum if minimum is not None else default_floor
            # X's figure caps the default floor, so X always qualifies. Bowling reads X's figures up
            # front; batting and fielding read them only when X is missing, so comparisons where X
            # qualifies cost no request. The player page has no team filter, so under one its figure
            # covers all of X's teams: exact for one team, an upper bound for more.
            target_floor_read = minimum is None and discipline_value == "bowling"
            if target_floor_read:
                floor_value = _floor_for_target(floor_value, await target_floor())
            query = comparison_query(floor_value)
            try:
                pages = await _fetch_all_result_pages(call, query, fetcher, settings)
            except TooBroadError as error:
                if minimum is None:
                    raise
                # Leaving minimum out helps only when crickey's own floor is higher (#81).
                fix = (
                    "leave minimum out so crickey picks a floor, or raise it"
                    if minimum < default_floor
                    else "raise it"
                )
                raise TooBroadError(
                    f"{error} The minimum ({min_field} >= {minimum}) lets in too many "
                    f"players: {fix}."
                ) from error
            result_pages_used = len(pages)
            target = _target_row(pages, target_id)
            if target is None and minimum is None and not target_floor_read:
                # The lower-floor table holds every row of this one, so it needs as many pages.
                if 2 * result_pages_used > settings.max_pages:
                    raise TooBroadError(
                        f"That query is too broad: {target_label()} was not in "
                        f"{rows_text} ({min_field} >= {floor_value}), and a lower "
                        f"minimum needs at least {result_pages_used} more pages, but this call "
                        f"has {settings.max_pages - result_pages_used} of its "
                        f"{settings.max_pages} pages left; pass minimum to set one."
                    )
                lowered_floor = _floor_for_target(floor_value, await target_floor())
                if lowered_floor < floor_value:
                    floor_value = lowered_floor
                    query = comparison_query(floor_value)
                    pages = await _fetch_all_result_pages(
                        call,
                        query,
                        fetcher,
                        settings,
                        max_pages=settings.max_pages - result_pages_used,
                    )
                    result_pages_used += len(pages)
                    target = _target_row(pages, target_id)
            if target is None and minimum is None and "team" in filters["query"]:
                raise ToolError(
                    f"{target_label(start=True)} was not in {rows_text} "
                    f"({min_field} >= {floor_value}). {target_label(start=True)}'s {min_field} "
                    "for that team can't be read from the player page, which has no team filter; "
                    "pass minimum to set a lower one."
                )
            if target is None and minimum is not None and minimum > 0:
                raise ToolError(
                    f"{target_label(start=True)} was not in {rows_text} "
                    f"({min_field} >= {minimum}), so {target_label()} has less than that "
                    "minimum. Lower minimum, or leave it out so crickey picks a floor."
                )
            if target is None:
                raise ToolError(f"{target_label(start=True)} was not in {rows_text}.")
            fetched_name = target.get("player_name") or page_name
            player = matched or _player_by_id(target_id, fetched_name)
            floor_lowered = minimum is None and floor_value < default_floor
            all_rows = [row for page in pages for row in page.table.to_dict("records")]
            target_values = tuple(
                metric.display_value(metric.value_from_row(target, class_id=class_id))
                for metric in metrics
            )
            comparison_rows: list[tuple[dict[str, Any], str, list[int]]] = []
            for row in all_rows:
                comparisons = [
                    metric.compare(metric.value_from_row(row, class_id=class_id), target_value)
                    for metric, target_value in zip(metrics, target_values, strict=True)
                ]
                if _row_id(row) == player.player_id:
                    comparison_rows.append((row, "target", []))
                    continue
                beats = (
                    all(value > 0 for value in comparisons)
                    if match_mode == "all"
                    else any(value > 0 for value in comparisons)
                )
                level = match_mode == "all" and all(value >= 0 for value in comparisons)
                if beats:
                    comparison_rows.append((row, "beats", comparisons))
                elif level:
                    comparison_rows.append((row, "level", comparisons))
            comparison_rows = sorted(
                comparison_rows,
                key=lambda item: tuple(
                    rank_key(metric, metric.value_from_row(item[0], class_id=class_id))
                    for metric in metrics
                ),
            )
            proof_thresholds = (
                ()
                if match_mode == "any"
                else tuple(
                    Threshold(metric, value)
                    for metric, value in zip(metrics, target_values, strict=True)
                    if value is not None
                )
            )
            proof = await build_proof_link(
                query,
                thresholds=proof_thresholds,
                expected_player_ids=tuple(
                    _row_id(row)
                    for row, _relation, _comparisons in comparison_rows
                    if _row_id(row) is not None
                ),
                call=call,
                as_of=_today(fetcher),
                page_allowance=max(0, settings.max_pages - result_pages_used),
                fallback_reason=(
                    "Statsguru cannot express OR across qualifications"
                    if match_mode == "any"
                    else None
                ),
            )
    except (
        FetcherError,
        StatsguruParseError,
        ValidationError,
        QuerySpecError,
        ValueError,
    ) as error:
        raise ToolError(
            _validation_message(error) if isinstance(error, ValidationError) else str(error)
        ) from error

    payload_rows = [
        _comparison_row_payload(
            row,
            metrics,
            class_id,
            player.player_id,
            relation,
            comparisons,
        )
        for row, relation, comparisons in comparison_rows
    ]
    as_of = _today(fetcher)
    metric_labels = tuple(metric.label for metric in metrics)
    name_note = _name_mismatch_note(player_name, player_id, fetched_name)
    answer = render_answer(
        AnswerRenderInput(
            short_answer=_comparison_short_answer(player.name, metrics, payload_rows, match_mode),
            table=RenderedTable(
                headers=("Player", *metric_labels, "Relation"),
                rows=tuple(
                    (row["player"], *(row["values"][m.key] for m in metrics), row["detail"])
                    for row in payload_rows
                ),
            ),
            method=(
                "Compared Statsguru displayed values; equal displayed values count as ties.",
                _direction_method(*metrics),
                (
                    f"Required {'all' if match_mode == 'all' else 'any'} metric(s) "
                    "to beat the player; all-metric ties are reported separately."
                ),
            ),
            assumptions=(
                *((name_note,) if name_note else ()),
                f"Minimum: {query.qualifications[0].field} >= {query.qualifications[0].minimum}.",
                *(
                    (
                        f"Lowered from the default {min_field} >= {default_floor} to "
                        f"{player.name}'s own figure, so {player.name} qualifies.",
                    )
                    if floor_lowered
                    else ()
                ),
                f"Period: {_period_text(period_value, as_of=as_of)}.",
                *_fielding_notes(discipline_value, metrics),
            ),
            proof_links=(proof,),
            players=tuple(
                RenderPlayer(row["player"], row["player_id"])
                for row in payload_rows
                if row.get("player_id") is not None
            ),
            as_of=as_of,
            current_or_recent_matches=pages[0].current_or_recent_matches,
        )
    )
    return _tool_result(
        "Built player comparison.",
        {
            "status": "ok",
            "answer_markdown": answer,
            "player": _candidate_payload(player),
            "metrics": [metric.key for metric in metrics],
            "rows": payload_rows,
            "beaters": [row for row in payload_rows if row["relation"] == "beats"],
            "level": [row for row in payload_rows if row["relation"] == "level"],
            "ties": [
                row for row in payload_rows if row["relation"] == "level" and not row["better_on"]
            ],
            "proof": _jsonable(proof),
            "request_pages": result_pages_used,
        },
    )


async def _player_record_tool(
    fetcher: Fetcher,
    *,
    player_name: str | None,
    player_id: int | None,
    format: str | int,
    discipline: RecordDiscipline | str,
    period: str | None,
    opposition: str | list[str] | None,
    host_country: str | list[str] | None,
    continent: str | list[str] | None,
    ground: str | list[str] | None,
    trophy: str | list[str] | None,
    home_or_away: str | int | None,
    match_result: str | int | None,
    split_by: SplitBy | str | None,
    ctx: Context | None,
) -> CallToolResult:
    query_name = _require_player(player_name, player_id)
    try:
        class_id = _format_class(format)
        discipline_value = _record_discipline(discipline)
        split_key = _split_by(split_by)
        requested_period = _parse_period(period)
    except ValueError as error:
        raise ToolError(str(error)) from error
    progress = _progress_callback(ctx)
    by_id = player_id is not None
    try:
        async with fetcher.call(budget=FETCH_BUDGET_SECONDS, progress=progress) as call:
            if player_id is not None:
                target_id = player_id
                matched: PlayerCandidate | None = None
            else:
                names = NameResolver(fetcher)
                resolution = await names.resolve_player(query_name, class_id=class_id, call=call)
                if resolution.needs_clarification or resolution.match is None:
                    return _clarification_result(
                        query_name, resolution, class_id=class_id, tool="player_record"
                    )
                matched = resolution.match
                target_id = matched.player_id
            # By ID, errors name the player only from Statsguru's pages, never from player_name.
            known_name = matched.name if matched is not None else None
            with _target_page_errors(
                target_id, class_id, discipline_value, name=known_name, by_id=by_id
            ):
                period_value = await _player_period(
                    fetcher, target_id, class_id, requested_period, call
                )
            filters = await _answer_filters(
                fetcher,
                class_id,
                call=call,
                opposition=opposition,
                host_country=host_country,
                continent=continent,
                ground=ground,
                trophy=trophy,
                home_or_away=home_or_away,
                match_result=match_result,
            )
            if filters.get("needs_clarification"):
                return _tool_result("A filter needs clarification.", filters)
            spec = PlayerPageSpec(
                player_id=target_id,
                **{
                    "class": class_id,
                    "type": discipline_value,
                    "period": period_value,
                    **filters["player_page"],
                },
            )
            as_of = _today(fetcher)
            url = spec.url(as_of=as_of)
            with _target_page_errors(
                target_id, class_id, discipline_value, name=known_name, by_id=by_id
            ):
                html = await call.fetch(
                    url,
                    freshness=_freshness_for_query(
                        StatsguruQuery(
                            **{"class": class_id, "type": discipline_value, "period": period_value}
                        ),
                        as_of,
                    ),
                )
                page = parse_player_page(html)
            row = _player_record_row(page)
            # A split reads the grouped rows on the same page, so it costs no request.
            grouped = parse_grouped_rows(html) if split_key and row is not None else None
            recent = parse_current_or_recent_matches(html)
    except (
        FetcherError,
        StatsguruParseError,
        ValidationError,
        QuerySpecError,
        ValueError,
    ) as error:
        raise ToolError(
            _validation_message(error) if isinstance(error, ValidationError) else str(error)
        ) from error

    player = matched or _player_by_id(target_id, page.player_name)
    name_note = _name_mismatch_note(player_name, player_id, page.player_name)
    assumptions = (
        *((name_note,) if name_note else ()),
        f"Period: {_period_text(period_value, as_of=as_of)}.",
    )
    proof = ProofLink(spec.label(as_of=as_of), url, True, True, row_count=len(page.career_averages))
    if row is None:
        answer = render_answer(
            AnswerRenderInput(
                short_answer=f"No matches found for {player.name} with those filters.",
                table=RenderedTable(
                    headers=("Player", "Result"), rows=((player.name, "No matches"),)
                ),
                method=(
                    f"Read the player's Statsguru {discipline_value} page with the same filters.",
                ),
                assumptions=assumptions,
                proof_links=(proof,),
                players=(RenderPlayer(player.name, player.player_id),),
                as_of=as_of,
                current_or_recent_matches=recent,
            )
        )
        return _tool_result(
            "No matching player record.",
            {
                "status": "no_matches",
                "answer_markdown": answer,
                "player": _candidate_payload(player),
                "row": None,
                **({"split": None} if split_key else {}),
                "proof": _jsonable(proof),
            },
        )

    columns = _player_record_columns(page)
    values = {column: _display_value(row, column) for column in columns}
    if split_key and grouped is not None:
        return _player_split_result(
            player,
            class_id=class_id,
            discipline=discipline_value,
            split_key=split_key,
            grouped=grouped,
            record=values,
            assumptions=assumptions,
            record_proof=proof,
            as_of=as_of,
            recent=recent,
            max_pages=fetcher.settings.max_pages,
        )
    summary_values = _player_record_summary(player.name, values, discipline_value)
    answer = render_answer(
        AnswerRenderInput(
            short_answer=summary_values,
            table=RenderedTable(
                headers=tuple(columns), rows=(tuple(values[column] for column in columns),)
            ),
            method=(f"Read the player's Statsguru {discipline_value} page with the same filters.",),
            assumptions=assumptions,
            proof_links=(proof,),
            players=(RenderPlayer(player.name, player.player_id),),
            as_of=as_of,
            current_or_recent_matches=recent,
        )
    )
    return _tool_result(
        "Built player record.",
        {
            "status": "ok",
            "answer_markdown": answer,
            "player": _candidate_payload(player),
            "row": values,
            "proof": _jsonable(proof),
        },
    )


def _format_class(value: str | int) -> int:
    # Small models also write Statsguru's class number as text, such as "2".
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value)
    if isinstance(value, int):
        if value in _DEFAULT_FIND_CLASSES:
            return value
        raise ToolError(
            f"format class must be one of {', '.join(map(str, _DEFAULT_FIND_CLASSES))}."
        )
    normalized = " ".join(value.casefold().replace("-", " ").split())
    try:
        return _FORMAT_CLASSES[normalized]
    except KeyError as error:
        raise ToolError("format must be Test, ODI, T20I, all T20 or all internationals.") from error


def _answer_discipline(
    value: AnswerDiscipline | RecordDiscipline | str,
    *,
    allowed: tuple[str, ...] = _ANSWER_DISCIPLINES,
) -> str:
    normalized = str(value.value if isinstance(value, StrEnum) else value).casefold()
    if normalized in allowed:
        return normalized
    choices = _metric_list([f"'{discipline}'" for discipline in allowed], conjunction="or")
    raise ValueError(f"discipline must be {choices}.")


def _record_discipline(value: RecordDiscipline | str) -> str:
    # player_record stays batting and bowling: its splits count hundreds or five-wicket hauls, and
    # fielding has no equivalent yet. A fielding call is pointed elsewhere.
    if str(value.value if isinstance(value, StrEnum) else value).casefold() == "fielding":
        raise ValueError(
            "player_record has no fielding records yet; use query_stats with type fielding "
            "for one player's figures, or better_than_player to compare players."
        )
    return _answer_discipline(value, allowed=_RECORD_DISCIPLINES)


def _answer_metric(discipline: str, value: AnswerMetric | str) -> Metric:
    normalized = str(value.value if isinstance(value, AnswerMetric) else value)
    normalized = " ".join(normalized.casefold().replace("_", " ").replace("-", " ").split())
    registry = {
        "batting": (
            "runs",
            "average",
            "strike_rate",
            "hundreds",
            "fifties",
            "innings_per_hundred",
            "innings_per_fifty_plus",
            "balls_per_dismissal",
        ),
        "bowling": (
            "wickets",
            "bowling_average",
            "economy_rate",
            "bowling_strike_rate",
            "five_wickets",
            "ten_wickets",
        ),
        "fielding": (
            "catches",
            "fielder_catches",
            "keeper_catches",
            "stumpings",
            "dismissals",
            "dismissals_per_innings",
        ),
    }[discipline]
    aliases = {
        "batting": {
            "average": "average",
            "strike rate": "strike rate",
        },
        "bowling": {
            "average": "bowling average",
            "economy": "economy rate",
            "economy rate": "economy rate",
            "strike rate": "bowling strike rate",
            "five fors": "five wickets",
            "five for": "five wickets",
            "five wickets": "five wickets",
        },
        # Statsguru's own field names and common wordings.
        "fielding": {
            "caught": "catches",
            "caught fielder": "fielder catches",
            "outfield catches": "fielder catches",
            "catches as fielder": "fielder catches",
            "caught keeper": "keeper catches",
            "wicketkeeper catches": "keeper catches",
            "wicket keeper catches": "keeper catches",
            "catches as wicketkeeper": "keeper catches",
            "catches as keeper": "keeper catches",
            "stumped": "stumpings",
            "dismissals per inns": "dismissals per innings",
            "d/i": "dismissals per innings",
        },
    }[discipline]
    normalized = aliases.get(normalized, normalized)
    getter = {
        "batting": batting_metric,
        "bowling": bowling_metric,
        "fielding": fielding_metric,
    }[discipline]
    for metric in (getter(key) for key in registry):
        labels = {
            metric.key.casefold().replace("_", " "),
            metric.label.casefold(),
        }
        if normalized in labels:
            return metric
    labels = ", ".join(getter(key).label for key in registry)
    raise ValueError(
        f"unknown {discipline} metric {value!r}; valid keys: {', '.join(registry)}; "
        f"labels: {labels}"
    )


async def _answer_filters(
    fetcher: Fetcher,
    class_id: int,
    *,
    call,
    team: str | list[str] | None = None,
    opposition: str | list[str] | None = None,
    host_country: str | list[str] | None = None,
    continent: str | list[str] | None = None,
    ground: str | list[str] | None = None,
    trophy: str | list[str] | None = None,
    home_or_away: str | int | None = None,
    match_result: str | int | None = None,
) -> dict[str, Any]:
    resolver = NameResolver(fetcher)
    query: dict[str, Any] = {}
    player_page: dict[str, Any] = {}
    clarifications: list[dict[str, Any]] = []

    def add_lookup(
        field: str, results: Iterable[LookupResult], *, for_player_page: bool = True
    ) -> None:
        results = tuple(results)
        values = []
        for result in results:
            if result.match is None:
                clarifications.append(_lookup_payload(result))
                continue
            values.append(result.match.value)
        if values and not any(result.match is None for result in results):
            value: int | tuple[int, ...] = values[0] if len(values) == 1 else tuple(values)
            query[field] = value
            if for_player_page:
                player_page[field] = value
            return

    if team is not None:
        add_lookup(
            "team",
            (resolver.resolve_team(item, class_id=class_id) for item in _filter_items(team)),
            for_player_page=False,
        )
    if opposition is not None:
        add_lookup(
            "opposition",
            (resolver.resolve_team(item, class_id=class_id) for item in _filter_items(opposition)),
        )
    if host_country is not None:
        add_lookup("host", (lookup_host(class_id, item) for item in _filter_items(host_country)))
    if continent is not None:
        add_lookup(
            "continent", (lookup_continent(class_id, item) for item in _filter_items(continent))
        )
    if trophy is not None:
        add_lookup(
            "trophy",
            (resolver.resolve_trophy(item, class_id=class_id) for item in _filter_items(trophy)),
        )
    if ground is not None:
        add_lookup(
            "ground",
            tuple(
                [
                    await resolver.resolve_ground(item, class_id=class_id, call=call)
                    for item in _filter_items(ground)
                ]
            ),
        )
    if home_or_away is not None:
        value = _choice_value(home_or_away, {"home": 1, "away": 2, "neutral": 3})
        query["home_or_away"] = value
        player_page["home_or_away"] = value
    if match_result is not None:
        value = _choice_value(
            match_result, {"won": 1, "lost": 2, "tied": 3, "drawn": 4, "no result": 5}
        )
        query["result"] = value
        player_page["result"] = value
    if clarifications:
        return {
            "status": "needs_clarification",
            "needs_clarification": True,
            "candidates": clarifications,
        }
    return {"query": query, "player_page": player_page}


def _choice_value(value: str | int, choices: Mapping[str, int]) -> int:
    if isinstance(value, int):
        return value
    normalized = " ".join(value.casefold().replace("-", " ").split())
    try:
        return choices[normalized]
    except KeyError as error:
        raise ValueError(
            f"unknown filter value {value!r}; expected {', '.join(choices)}"
        ) from error


def _filter_items(value: str | list[str]) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    return (str(value),)


def _has_text(value: str | None) -> bool:
    return value is not None and bool(value.strip())


# The period forms crickey reads. Matching ignores case, reads underscores as spaces and
# any dash as a hyphen, since models write periods those ways.
_CAREER = SymbolicPeriod(kind=SymbolicPeriodKind.CAREER)
_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}
_DASHES = str.maketrans(dict.fromkeys("\u2010\u2011\u2012\u2013\u2014\u2015\u2212", "-"))
_PERIOD_DATE = r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2} [a-z]+ \d{4}"
_PERIOD_TO = r"\s*(?:-|to|until|through|and)\s*"
_ALL_TIME_PERIOD = re.compile(r"all[ -]?time")
_CAREER_PERIOD = re.compile(
    r"(?:(?:the|his|her|their) )?(?:(?:whole|full|entire) )?career(?: span)?"
)
_PLAYER_YEARS_PERIOD = re.compile(
    rf"(?:the )?(?P<side>first|last) (?P<count>\d+|{'|'.join(_NUMBER_WORDS)}) years?"
    r"(?: of (?:(?:his|her|their|the) )?career)?"
)
# Also "last N years" as the descriptions write it, with N left in.
_UNCOUNTED_YEARS_PERIOD = re.compile(
    r"(?:the )?(?:first|last) (?:n )?years?(?: of (?:(?:his|her|their|the) )?career)?"
)
_DATES_PERIOD = re.compile(
    rf"(?:from |between )?(?P<start>{_PERIOD_DATE}){_PERIOD_TO}(?P<end>{_PERIOD_DATE})"
)
# "from" and "between" only with an end: "from 2000" alone means 2000 onwards, which isn't a form.
_YEARS_PERIOD = re.compile(
    rf"(?:in )?(?P<year>\d{{4}})|(?:from |between )?(?P<start>\d{{4}}){_PERIOD_TO}(?P<end>\d{{4}})"
)
_SEASON_PERIOD = re.compile(
    r"season (?P<year>\d{4})(?:\s*[/-]\s*(?P<next>\d{2}|\d{4}))?"
    r"|(?P<bare>\d{4})\s*/\s*(?P<bare_next>\d{2}|\d{4})"
)


def _parse_period(text: str | None, *, without_player: str | None = None) -> Period:
    """The period a tool call gives as text; None means all time.

    A bare year is the calendar year, and a season needs the word or the 2011/12 form.
    `without_player` says why a tool has no player whose career a period could follow, such as
    "leaderboard ranks every player".
    """
    for_player = without_player is None
    if text is None or not text.strip():
        return None
    words = " ".join(text.casefold().replace("_", " ").translate(_DASHES).split()).strip(".\"' ")
    period: Period
    if _ALL_TIME_PERIOD.fullmatch(words):
        return None
    if _CAREER_PERIOD.fullmatch(words):
        period = _CAREER
    elif match := _PLAYER_YEARS_PERIOD.fullmatch(words):
        count = match["count"]
        years = _NUMBER_WORDS[count] if count in _NUMBER_WORDS else int(count)
        if years < 1:
            raise QuerySpecError(f'period "{text}" needs at least 1 year.')
        side = (
            SymbolicPeriodKind.FIRST_YEARS
            if match["side"] == "first"
            else SymbolicPeriodKind.LAST_YEARS
        )
        period = SymbolicPeriod(kind=side, years=years)
    elif _UNCOUNTED_YEARS_PERIOD.fullmatch(words):
        raise QuerySpecError(
            f'period "{text}" needs a number of years: put the question\'s number in place of N, '
            'as in "last N years".'
        )
    elif match := _DATES_PERIOD.fullmatch(words):
        start = _period_date(text, match["start"])
        period = _dated_period(text, start, _period_date(text, match["end"]))
    elif match := _YEARS_PERIOD.fullmatch(words):
        first_year, last_year = (
            int(match["year"] or match["start"]),
            int(match["year"] or match["end"]),
        )
        period = _dated_period(text, date(first_year, 1, 1), date(last_year, 12, 31))
    elif match := _SEASON_PERIOD.fullmatch(words):
        year = match["year"] or match["bare"]
        period = SeasonPeriod(season=_season(text, year, match["next"] or match["bare_next"]))
    else:
        raise QuerySpecError(
            f'period "{text}" can\'t be read; use {_period_forms(for_player=for_player)}.'
        )
    if isinstance(period, SymbolicPeriod) and not for_player:
        raise QuerySpecError(
            f'period "{text}" follows one player\'s career, but {without_player}; '
            f"use {_period_forms(for_player=False)}."
        )
    return period


def _period_forms(*, for_player: bool) -> str:
    spans = f'"career", {_PLAYER_SPANS}, ' if for_player else ""
    return f'"all time", {spans}{_PERIOD_RANGES}'


def _period_date(text: str, value: str) -> date:
    for pattern in ("%Y-%m-%d", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(value, pattern).date()
        except ValueError:
            continue
    raise QuerySpecError(f'period "{text}" has a date that can\'t be read: "{value}".')


def _dated_period(text: str, start: date, end: date) -> ResolvedPeriod:
    if start > end:
        raise QuerySpecError(f'period "{text}" ends before it starts.')
    return ResolvedPeriod(start=start, end=end)


def _season(text: str, year: str, following: str | None) -> str:
    """Statsguru's name for a season: a year, or two years in a row, as in 2011/12."""
    if following is None:
        return year
    next_year = int(year) + 1
    if following not in {str(next_year), f"{next_year % 100:02d}"}:
        raise QuerySpecError(
            f'period "{text}" isn\'t a season; a season is a year or two years in a row, '
            'such as "season 2011" or "season 2011/12".'
        )
    return f"{year}/{next_year % 100:02d}"


async def _player_period(
    fetcher: Fetcher, player_id: int, class_id: int, period: Period, call
) -> Period:
    """The period, with a span of the player's career resolved to dates (plan › Calculations)."""
    if isinstance(period, SymbolicPeriod):
        return await PeriodResolver(fetcher).resolve_symbolic(
            player_id, class_id=class_id, period=period, call=call
        )
    return period


def _period_text(period: Period, *, as_of: date) -> str:
    if isinstance(period, ResolvedPeriod):
        return f"{period.start.isoformat()} to {period.end.isoformat()}"
    if isinstance(period, SeasonPeriod):
        return f"season {period.season}"
    if isinstance(period, SymbolicPeriod):
        if period.kind == SymbolicPeriodKind.CAREER:
            return "career span"
        return f"{period.kind.value.replace('_', ' ')} ({period.years} years)"
    return f"all time through {as_of.isoformat()}"


def _leaderboard_default_minimum(
    metric: Metric, class_id: int, *, period: Period, filters: Mapping[str, Any]
) -> DefaultMinimum:
    if metric.is_count:
        assert metric.qualval is not None
        return DefaultMinimum(metric.qualval, _COUNT_LEADERBOARD_MINIMUM)
    return _rate_default_minimum(metric, class_id, period=period, filters=filters)


def _rate_default_minimum(
    metric: Metric, class_id: int, *, period: Period, filters: Mapping[str, Any]
) -> DefaultMinimum:
    if metric.key in _BOWLING_RATE_METRICS and _is_filtered_query(period, filters):
        return DefaultMinimum("wickets", _FILTERED_BOWLING_RATE_MINIMUMS[class_id])
    if _is_fielding_metric(metric):
        return _fielding_default_minimum(metric, class_id, period=period, filters=filters)
    return metric.default_minimum(class_id)


def _fielding_default_minimum(
    metric: Metric, class_id: int, *, period: Period, filters: Mapping[str, Any]
) -> DefaultMinimum:
    # The same field as the registry's floor, lower under any narrowing filter, like bowling rates.
    minimum = metric.default_minimum(class_id)
    if _is_filtered_query(period, filters):
        return DefaultMinimum(minimum.field, _FILTERED_FIELDING_MINIMUMS[class_id])
    return minimum


def _is_fielding_metric(metric: Metric) -> bool:
    return FIELDING_METRICS.get(metric.key) is metric


def _comparison_default_minimum(
    metrics: tuple[Metric, ...],
    class_id: int,
    *,
    period: Period = None,
    filters: Mapping[str, Any] | None = None,
) -> tuple[str, int | Decimal]:
    if any(metric.key in {"average", "strike_rate"} for metric in metrics):
        minimum = batting_metric("runs").default_minimum(class_id)
        return minimum.field, max(Decimal(minimum.minimum), Decimal(1000))
    if _uses_bowling_rate_minimum(metrics):
        minimum = _rate_default_minimum(
            bowling_metric("wickets")
            if not any(metric.key in _BOWLING_RATE_METRICS for metric in metrics)
            else next(metric for metric in metrics if metric.key in _BOWLING_RATE_METRICS),
            class_id,
            period=period,
            filters=filters or {},
        )
        return minimum.field, minimum.minimum
    if _is_fielding_metric(metrics[0]):
        # A rate takes its dismissals floor; counts take the first metric's own field.
        metric = next(
            (metric for metric in metrics if metric.key in _FIELDING_RATE_METRICS), metrics[0]
        )
        minimum = _fielding_default_minimum(metric, class_id, period=period, filters=filters or {})
        return minimum.field, minimum.minimum
    minimum = metrics[0].default_minimum(class_id)
    return minimum.field, minimum.minimum


def _uses_bowling_rate_minimum(metrics: tuple[Metric, ...]) -> bool:
    return any(metric.key in _BOWLING_RATE_METRICS for metric in metrics)


def _is_filtered_query(period: Period, filters: Mapping[str, Any]) -> bool:
    return period is not None or any(
        key in filters
        for key in (
            "continent",
            "ground",
            "home_or_away",
            "host",
            "opposition",
            "result",
            "team",
            "trophy",
        )
    )


async def _target_filtered_floor(
    call,
    player_id: int,
    *,
    class_id: int,
    discipline: str,
    period: Period,
    filters: Mapping[str, Any],
    field: str,
    fetcher: Fetcher,
) -> tuple[Decimal | None, str | None]:
    column = _FIELD_COLUMNS[discipline].get(field)
    if column is None:
        return None, None
    spec = PlayerPageSpec(
        player_id=player_id,
        **{
            "class": class_id,
            "type": discipline,
            "period": period,
            **filters,
        },
    )
    as_of = _today(fetcher)
    html = await call.fetch(
        spec.url(as_of=as_of),
        freshness=_freshness_for_query(
            StatsguruQuery(**{"class": class_id, "type": discipline, "period": period}),
            as_of,
        ),
    )
    page = parse_player_page(html)
    row = _player_record_row(page)
    if row is None:
        return None, page.player_name
    return _decimal(row.get(column)), page.player_name


def _floor_for_target(floor: int | Decimal, target_value: Decimal | None) -> int | Decimal:
    return floor if target_value is None else min(Decimal(floor), target_value)


def _target_row(pages: tuple[ResultsPage, ...], player_id: int) -> dict[str, Any] | None:
    return next(
        (
            row
            for page in pages
            for row in page.table.to_dict("records")
            if _row_id(row) == player_id
        ),
        None,
    )


def _direction_method(*metrics: Metric) -> str:
    lower = [metric.label for metric in metrics if metric.direction == BetterDirection.LOWER]
    higher = [metric.label for metric in metrics if metric.direction == BetterDirection.HIGHER]
    pieces = []
    if lower:
        pieces.append(f"Lower {_metric_list(lower)} {'is' if len(lower) == 1 else 'are'} better")
    if higher:
        pieces.append(f"Higher {_metric_list(higher)} {'is' if len(higher) == 1 else 'are'} better")
    return "; ".join(pieces) + "."


def _fielding_notes(discipline: str, metrics: Iterable[Metric]) -> tuple[str, ...]:
    if discipline != "fielding":
        return ()
    notes = [_FIELDING_NOTE]
    for metric in metrics:
        note = _FIELDING_METRIC_NOTES.get(metric.key)
        if note is not None and note not in notes:
            notes.append(note)
    return tuple(notes)


async def _fetch_all_result_pages(
    call,
    query: StatsguruQuery,
    fetcher: Fetcher,
    settings: Settings,
    *,
    max_pages: int | None = None,
) -> tuple[ResultsPage, ...]:
    limit = settings.max_pages if max_pages is None else max_pages
    limit_text = (
        f"the limit is {settings.max_pages}"
        if limit == settings.max_pages
        else f"this call has {limit} of its {settings.max_pages} pages left"
    )
    as_of = _today(fetcher)
    pages = [
        parse_results_page(
            await call.fetch(
                query.results_url(as_of=as_of), freshness=_freshness_for_query(query, as_of)
            )
        )
    ]
    total_pages = pages[0].totals.pages or 1
    if total_pages > limit:
        raise TooBroadError(
            f"That query is too broad: it needs {total_pages} pages, but {limit_text}."
        )
    for page_number in range((pages[0].totals.page or 1) + 1, total_pages + 1):
        page_query = query.model_copy(update={"page": page_number})
        pages.append(
            parse_results_page(
                await call.fetch(
                    page_query.results_url(as_of=as_of),
                    freshness=_freshness_for_query(query, as_of),
                )
            )
        )
    return tuple(pages)


async def _fetch_leaderboard_pages(
    call,
    query: StatsguruQuery,
    fetcher: Fetcher,
    settings: Settings,
    metric: Metric,
    class_id: int,
    top_n: int,
) -> tuple[ResultsPage, ...]:
    if metric.is_derived:
        return await _fetch_all_result_pages(call, query, fetcher, settings)
    as_of = _today(fetcher)
    pages = [
        parse_results_page(
            await call.fetch(
                query.results_url(as_of=as_of), freshness=_freshness_for_query(query, as_of)
            )
        )
    ]
    while len(pages) < settings.max_pages and _needs_boundary_tie_page(
        pages, query, metric, class_id, top_n
    ):
        next_page = (pages[-1].totals.page or len(pages)) + 1
        page_query = query.model_copy(update={"page": next_page})
        pages.append(
            parse_results_page(
                await call.fetch(
                    page_query.results_url(as_of=as_of),
                    freshness=_freshness_for_query(query, as_of),
                )
            )
        )
    if _needs_boundary_tie_page(pages, query, metric, class_id, top_n):
        raise TooBroadError(
            f"That query is too broad: the tie at rank {top_n} continues past "
            f"the {settings.max_pages}-page limit."
        )
    return tuple(pages)


def _needs_boundary_tie_page(
    pages: list[ResultsPage],
    query: StatsguruQuery,
    metric: Metric,
    class_id: int,
    top_n: int,
) -> bool:
    current_page = pages[-1].totals.page or len(pages)
    total_pages = pages[-1].totals.pages or current_page
    if current_page >= total_pages:
        return False
    rows = _ranked_rows(tuple(pages), metric, class_id)
    if len(rows) < top_n or not rows:
        return True
    if len(rows) < query.size:
        return False
    boundary = rows[top_n - 1]
    last = rows[-1]
    return metric.tied(
        metric.value_from_row(boundary, class_id=class_id),
        metric.value_from_row(last, class_id=class_id),
    )


def _ranked_rows(
    pages: tuple[ResultsPage, ...], metric: Metric, class_id: int
) -> list[dict[str, Any]]:
    rows = [row for page in pages for row in page.table.to_dict("records")]
    return sorted(
        rows, key=lambda row: rank_key(metric, metric.value_from_row(row, class_id=class_id))
    )


def _select_ranked_with_ties(
    rows: list[dict[str, Any]], metric: Metric, class_id: int, top_n: int
) -> list[tuple[dict[str, Any], int]]:
    selected: list[tuple[dict[str, Any], int]] = []
    previous_value: Decimal | None = None
    current_rank = 0
    boundary_rank: int | None = None
    for index, row in enumerate(rows, start=1):
        value = metric.value_from_row(row, class_id=class_id)
        if index == 1 or not metric.tied(value, previous_value):
            current_rank = index
        previous_value = value
        if current_rank <= top_n:
            selected.append((row, current_rank))
            boundary_rank = current_rank
            continue
        if boundary_rank is not None and current_rank == boundary_rank:
            selected.append((row, current_rank))
            continue
        break
    return selected


def _page_size_for_top_n(top_n: int) -> int:
    for size in (10, 25, 50, 100, 150, 200):
        if top_n < size:
            return size
    return 200


def _group_value(pages: tuple[ResultsPage, ...], metric: Metric) -> Decimal | None:
    if not metric.is_derived or metric.numerator_column is None:
        return None
    numerator = Decimal(0)
    denominator = Decimal(0)
    for page in pages:
        for row in page.table.to_dict("records"):
            num = _decimal(row.get(metric.numerator_column))
            parts = metric.denominator_columns or (metric.denominator_column,)
            den_parts = [_decimal(row.get(column)) for column in parts if column]
            den = None if any(part is None for part in den_parts) else sum(den_parts, Decimal(0))
            if num is not None and den is not None:
                numerator += num
                denominator += den
    if denominator == 0:
        return None
    return metric.display_value(numerator / denominator)


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "" or value == "-":
        return None
    try:
        return Decimal(str(value).replace(",", ""))
    except Exception:
        return None


def _row_id(row: Mapping[str, Any]) -> int | None:
    value = row.get("player_id")
    return int(value) if value is not None else None


def _metric_row_payload(
    row: Mapping[str, Any], metric: Metric, class_id: int, rank: int
) -> dict[str, Any]:
    value = metric.display_value(metric.value_from_row(row, class_id=class_id))
    payload = {
        "rank": rank,
        "player": row.get("player_name") or row.get("Player"),
        "player_id": _row_id(row),
        "value": value,
        "metric": metric.key,
    }
    for field in (
        "Inns",
        "100",
        "50",
        "Runs",
        "Ave",
        "SR",
        "BF",
        "Overs",
        "Mdns",
        "Wkts",
        "Econ",
        "5",
        "10",
        "Dis",
        "Ct",
        "St",
        "Ct Wk",
        "Ct Fi",
        "D/I",
    ):
        if field in row:
            payload[field] = _display_value(row, field)
    for qual_field, column in {
        "hundreds": "100",
        "innings": "Inns",
        "runs": "Runs",
        "balls_faced": "BF",
        "fifty_plus": "50",
        "wickets": "Wkts",
        "five_wickets": "5",
        "ten_wickets": "10",
        "dismissals": "Dis",
        "caught": "Ct",
        "stumped": "St",
        "caught_keeper": "Ct Wk",
        "caught_fielder": "Ct Fi",
        "dismissals_per_inns": "D/I",
    }.items():
        if column in row:
            payload[qual_field] = _display_value(row, column)
    return payload


def _comparison_row_payload(
    row: Mapping[str, Any],
    metrics: tuple[Metric, ...],
    class_id: int,
    target_id: int,
    relation: str,
    comparisons: list[int],
) -> dict[str, Any]:
    values = {
        metric.key: metric.display_value(metric.value_from_row(row, class_id=class_id))
        for metric in metrics
    }
    if _row_id(row) == target_id:
        better_on: list[str] = []
        level_on: list[str] = []
    else:
        better_on = [
            metric.key
            for metric, comparison in zip(metrics, comparisons, strict=True)
            if comparison > 0
        ]
        level_on = [
            metric.key
            for metric, comparison in zip(metrics, comparisons, strict=True)
            if comparison == 0
        ]
    return {
        "player": row.get("player_name") or row.get("Player"),
        "player_id": _row_id(row),
        "relation": "target" if _row_id(row) == target_id else relation,
        "better_on": [] if _row_id(row) == target_id else better_on,
        "level_on": [] if _row_id(row) == target_id else level_on,
        "detail": "target"
        if _row_id(row) == target_id
        else _comparison_detail(metrics, better_on, level_on),
        "values": values,
    }


def _comparison_detail(
    metrics: tuple[Metric, ...],
    better_on: list[str],
    level_on: list[str],
) -> str:
    labels = {metric.key: metric.label for metric in metrics}
    better = [labels[key] for key in better_on]
    level = [labels[key] for key in level_on]
    pieces = []
    if level:
        pieces.append("level on " + _metric_list(level))
    if better:
        pieces.append("better on " + _metric_list(better))
    return ", ".join(pieces) if pieces else "level"


def _comparison_short_answer(
    player_name: str,
    metrics: tuple[Metric, ...],
    payload_rows: list[dict[str, Any]],
    match_mode: str,
) -> str:
    beaters = [row for row in payload_rows if row["relation"] == "beats"]
    labels = [metric.label for metric in metrics]
    if match_mode == "any" and len(metrics) > 1:
        per_metric = _metric_list(
            f"{len([row for row in beaters if metric.key in row['better_on']])} on {metric.label}"
            for metric in metrics
        )
        return (
            f"{len(beaters)} player(s) beat {player_name}'s displayed "
            f"{_metric_list(labels, conjunction='or')}: {per_metric}."
        )
    level_count = len([row for row in payload_rows if row["relation"] == "level"])
    level_text = f" {level_count} player(s) were level with {player_name}." if level_count else ""
    return (
        f"{len(beaters)} player(s) beat {player_name}'s displayed "
        f"{' and '.join(labels)}.{level_text}"
    )


def _metric_list(labels: Iterable[str], *, conjunction: str = "and") -> str:
    items = tuple(labels)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + f" {conjunction} {items[-1]}"


def _player_record_row(page) -> Mapping[str, Any] | None:
    rows = page.career_averages.to_dict("records")
    filtered = [row for row in rows if str(row.get("Grouping", "")).casefold() == "filtered"]
    if filtered:
        return filtered[0]
    if any(str(row.get("Grouping", "")).casefold() == "unfiltered" for row in rows):
        return None
    return rows[0] if len(rows) == 1 else None


def _player_record_columns(page) -> list[str]:
    return [
        column
        for column in page.career_averages.columns
        if column not in _METADATA_COLUMNS and not column.endswith("_not_out")
    ]


def _player_record_summary(player_name: str, values: Mapping[str, Any], discipline: str) -> str:
    if discipline == "bowling":
        labels = (
            ("Mat", "matches"),
            ("Inns", "innings"),
            ("Wkts", "wickets"),
            ("Ave", "average"),
            ("Econ", "economy"),
            ("SR", "strike rate"),
            ("5", "five-wicket hauls"),
            ("10", "ten-wicket matches"),
        )
    else:
        labels = (
            ("Mat", "matches"),
            ("Inns", "innings"),
            ("Runs", "runs"),
            ("Ave", "average"),
            ("100", "hundreds"),
            ("50", "fifties"),
        )
    pieces = [f"{label} {values[column]}" for column, label in labels if column in values]
    if discipline == "bowling" and "BBI" in values:
        pieces.append(f"best innings {values['BBI']}")
    if "HS" in values:
        pieces.append(f"highest score {values['HS']}")
    return f"{player_name}'s record: {', '.join(pieces)}."


def _split_by(value: SplitBy | str | None) -> str | None:
    if value is None:
        return None
    text = str(value.value if isinstance(value, SplitBy) else value)
    normalized = "_".join(text.casefold().replace("-", " ").split())
    if not normalized:
        return None
    normalized = _SPLIT_ALIASES.get(normalized, normalized)
    if normalized not in _SPLITS:
        raise ValueError(f"split_by must be host, opposition, year or continent (got {text!r}).")
    return normalized


def _split_rows(grouped: pd.DataFrame, split_key: str) -> list[dict[str, Any]]:
    columns = [
        column
        for column in grouped.columns
        if column != "Grouping"
        and column not in GROUPING_COLUMNS
        and not column.endswith("_not_out")
    ]
    rows = []
    for row in grouped.to_dict("records"):
        if row["grouping_key"] != split_key:
            continue
        value = row["grouping_value"]
        rows.append(
            {
                "name": row["grouping_name"],
                "label": row["Grouping"],
                split_key: int(value) if value.isdigit() else value,
                **{column: _display_value(row, column) for column in columns},
            }
        )
    return rows


def _player_split_result(
    player: PlayerCandidate,
    *,
    class_id: int,
    discipline: str,
    split_key: str,
    grouped: pd.DataFrame,
    record: Mapping[str, Any],
    assumptions: tuple[str, ...],
    record_proof: ProofLink,
    as_of: date,
    recent: tuple[RecentMatch, ...],
    max_pages: int,
) -> CallToolResult:
    split = _SPLITS[split_key]
    count = _SPLIT_COUNTS[discipline]
    rows = _split_rows(grouped, split_key)
    matches = sum(row["Mat"] for row in rows if isinstance(row.get("Mat"), int))
    if not rows:
        raise ToolError(
            f"Statsguru's player page has no rows grouped by {split.noun} for this record, "
            "so crickey can't split it."
        )
    # Every match has one host, opposition, continent and year, so the grouped rows must add up to
    # the record. They do on every saved page, filtered or not; if they don't, they may not
    # cover the same filters, and showing them would be wrong.
    if matches != record.get("Mat"):
        raise ToolError(
            f"Statsguru's rows by {split.noun} add up to {matches} matches, not the record's "
            f"{record.get('Mat')}, so crickey can't confirm they cover the same filters and "
            f"period. Call player_record without split_by, or once per {split.noun} with "
            f"{split.filter_hint}."
        )
    shown = [column for column in _SPLIT_COLUMNS[discipline] if column in grouped.columns]
    # Groups without the counted figure, or None when the page has no such column.
    without = (
        [row["name"] for row in rows if not row.get(count.column)]
        if count.column in grouped.columns
        else None
    )
    # The record's own page proves the split too, so the proof keeps the record's row count, the
    # page's "Career averages" rows; "groups" counts the split's rows.
    proof = replace(
        record_proof, label=f"{record_proof.label}, Career summary rows by {split.noun}"
    )
    method = [
        f"Read the player's Statsguru {discipline} page with the same filters.",
        f"Took the rows its Career summary groups by {split.noun}"
        + (", which leaves out continents, a separate grouping." if split_key == "host" else "."),
    ]
    if without is not None:
        method.append(
            f"Counted the {split.plural} with at least one {count.singular} "
            f"(Statsguru's {count.column} column)."
        )
    lead = f"{player.name}'s {CLASS_LABELS[class_id]} {discipline} split by {split.noun}:"
    summary = _split_summary(split, count, rows, without)
    answer = render_answer(
        AnswerRenderInput(
            # Names such as "U.A.E." already end the sentence.
            short_answer=f"{lead} {summary}" + ("" if summary.endswith(".") else "."),
            table=RenderedTable(
                headers=(split.title, *shown),
                rows=tuple((row["name"], *(row.get(column) for column in shown)) for row in rows),
            ),
            method=tuple(method),
            assumptions=(
                *assumptions,
                f"Split: the {len(rows)} rows by {split.noun} add up to the record's {matches} "
                "matches, so they cover the same filters and period.",
                f"Scope: this player only. Finding every player with a {count.singular} "
                f"{split.preposition} every {split.noun} would need every player's page, beyond "
                f"the {max_pages}-page limit per call, so check players one at a time.",
            ),
            proof_links=(proof,),
            players=(RenderPlayer(player.name, player.player_id),),
            as_of=as_of,
            current_or_recent_matches=recent,
        )
    )
    counts = (
        {}
        if without is None
        else {f"with_{count.key}": len(rows) - len(without), f"without_{count.key}": without}
    )
    return _tool_result(
        "Built player record split.",
        {
            "status": "ok",
            "answer_markdown": answer,
            "player": _candidate_payload(player),
            "row": dict(record),
            "split": {
                "by": split_key,
                "rows": rows,
                "groups": len(rows),
                **counts,
                "matches": matches,
            },
            "proof": _jsonable(proof),
        },
    )


def _split_summary(
    split: _Split, count: _SplitCount, rows: list[dict[str, Any]], without: list[str] | None
) -> str:
    total = len(rows)
    where = split.preposition
    if without is None:
        return f"{total} {split.plural if total != 1 else split.noun}"
    if total == 1:
        found = f"no {count.plural}" if without else f"at least one {count.singular}"
        return f"{found} {where} the only {split.noun}, {rows[0]['name']}"
    if not without:
        return f"at least one {count.singular} {where} all {total} {split.plural}"
    if len(without) == total:
        return f"no {count.plural} {where} any of the {total} {split.plural}"
    return (
        f"at least one {count.singular} {where} {total - len(without)} of {total} "
        f"{split.plural}; none {where} {_metric_list(without)}"
    )


def _leaderboard_short_answer(
    metric: Metric, rows: list[dict[str, Any]], group_value: Decimal | None
) -> str:
    if not rows:
        return f"No qualifying players found for {metric.label}."
    leaders = [row for row in rows if row["rank"] == 1]
    leader_names = ", ".join(row["player"] for row in leaders)
    group_text = (
        f" The qualifying group overall figure is {group_value}." if group_value is not None else ""
    )
    if len(leaders) > 1:
        return (
            f"{leader_names} are tied for the lead with {leaders[0]['value']} "
            f"{metric.label}.{group_text}"
        )
    leader = leaders[0]
    return f"{leader['player']} leads with {leader['value']} {metric.label}.{group_text}"


def _leaderboard_fetch_method(
    metric: Metric, pages: tuple[ResultsPage, ...], settings: Settings
) -> str:
    if metric.is_derived:
        return f"Fetched every qualifying Statsguru row within the {settings.max_pages}-page limit."
    page_text = "page" if len(pages) == 1 else "pages"
    return (
        f"Fetched {len(pages)} sorted Statsguru result {page_text}; "
        "extra pages only cover boundary ties."
    )


def _clarification_result(name: str, resolution, *, class_id: int, tool: str) -> CallToolResult:
    summary = f"Found possible player candidates for {name!r}; please choose one."
    payload: dict[str, Any] = {
        "status": "needs_clarification",
        "query": name,
        "candidates": [_candidate_payload(candidate) for candidate in resolution.candidates],
    }
    if not payload["candidates"]:
        return _tool_result(summary, payload)
    payload["hint"] = f"Call {tool} again with player_id set to the chosen candidate's ID."
    label = CLASS_LABELS[class_id]
    rows = []
    for candidate in payload["candidates"]:
        fmt = next((fmt for fmt in candidate["formats"] if fmt["class"] == class_id), {})
        rows.append(
            (
                candidate["id"],
                candidate["name"],
                "/".join(candidate["country"]),
                fmt.get("span"),
                fmt.get("matches"),
            )
        )
    table = _markdown_table(("ID", "Name", "Country", f"{label} span", f"{label} matches"), rows)
    return _tool_result(summary, payload, text="\n".join([summary, payload["hint"], "", table]))


def _require_player(player_name: str | None, player_id: int | None) -> str:
    if player_id is None and not (player_name or "").strip():
        raise ToolError(
            "player_name or player_id is required; player_id is the ID from find_player "
            "or a needs_clarification candidate."
        )
    return player_name or ""


def _player_by_id(player_id: int, name: str | None) -> PlayerCandidate:
    return PlayerCandidate(player_id, name or f"Player ID {player_id}", None, (), ())


@contextmanager
def _target_page_errors(
    player_id: int, class_id: int, discipline: str, *, name: str | None, by_id: bool
) -> Iterator[None]:
    # Reading the target's own pages: a page with no records means the player has no record
    # in this format; by ID, a missing page means the same thing.
    try:
        yield
    except PlayerPageNoRecordsError as error:
        raise ToolError(
            _no_record_message(player_id, error.player_name or name, class_id, discipline)
        ) from error
    except UnavailableUrlError as error:
        if not by_id:
            raise
        raise ToolError(_no_record_message(player_id, name, class_id, discipline)) from error


def _no_record_message(player_id: int, name: str | None, class_id: int, discipline: str) -> str:
    who = f"Player ID {player_id}" + (f" ({name})" if name else "")
    return f"{who} has no {CLASS_LABELS[class_id]} {discipline} record on Statsguru."


def _name_mismatch_note(
    player_name: str | None, player_id: int | None, fetched_name: str | None
) -> str | None:
    # Only a by-ID call can clash, and only with a name Statsguru showed, not the ID fallback.
    given = (player_name or "").strip()
    if player_id is None or not given or not fetched_name or names_agree(given, fetched_name):
        return None
    return (
        f"Player: player_id {player_id} is {fetched_name} on Statsguru, not {given!r}; "
        "the answer follows player_id."
    )


def _lookup_payload(result: LookupResult) -> dict[str, Any]:
    return {
        "status": "needs_clarification",
        "query": result.query,
        "kind": result.kind,
        "candidates": [
            {"value": candidate.value, "name": candidate.name, "score": candidate.score}
            for candidate in result.candidates
        ],
    }


def _merge_candidate(
    existing: PlayerCandidate | None, candidate: PlayerCandidate
) -> PlayerCandidate:
    if existing is None:
        return candidate
    formats = {fmt.class_id: fmt for fmt in (*existing.formats, *candidate.formats)}
    return PlayerCandidate(
        existing.player_id,
        existing.name,
        existing.full_name,
        existing.country_codes,
        tuple(sorted(formats.values(), key=lambda fmt: fmt.class_id)),
    )


def _player_candidates_from_search(
    name: str, html: str, class_ids: tuple[int, ...], country: str | None
) -> tuple[dict[int, PlayerCandidate], bool]:
    candidates: dict[int, PlayerCandidate] = {}
    for class_id in class_ids:
        resolution = player_resolution_from_html(name, html, class_id=class_id, country=country)
        found = resolution.candidates
        if resolution.match is not None:
            found = (*found, resolution.match)
        for candidate in found:
            candidates[candidate.player_id] = _merge_candidate(
                candidates.get(candidate.player_id), candidate
            )
    if country is None or not candidates:
        return candidates, True
    filtered = {
        player_id: candidate
        for player_id, candidate in candidates.items()
        if _candidate_matches_country(candidate, country)
    }
    return (filtered, True) if filtered else (candidates, False)


def _candidate_matches_country(candidate: PlayerCandidate, country: str) -> bool:
    codes = country_codes(country)
    if not codes:
        return False
    return any(normalize_country(code) in codes for code in candidate.country_codes)


def _candidate_payload(candidate: PlayerCandidate) -> dict[str, Any]:
    return {
        "id": candidate.player_id,
        "name": candidate.name,
        "full_name": candidate.full_name,
        "country": list(candidate.country_codes),
        "formats": [_format_payload(fmt) for fmt in candidate.formats],
    }


def _format_payload(fmt: PlayerFormat) -> dict[str, Any]:
    return {
        "class": fmt.class_id,
        "label": fmt.label,
        "role": fmt.role,
        "span": _jsonable(fmt.span),
        "matches": fmt.match_count,
    }


def _find_player_summary(payload: Mapping[str, Any]) -> str:
    candidates = payload["candidates"]
    if not candidates:
        return f"No Statsguru player candidates found for {payload['query']!r}."
    if payload["status"] == "match":
        candidate = candidates[0]
        return f"Found {candidate['name']} ({candidate['id']})."
    return f"Found {len(candidates)} possible player candidates for {payload['query']!r}."


def _freshness_for_query(query: StatsguruQuery, as_of: date) -> Freshness:
    if isinstance(query.period, ResolvedPeriod):
        return freshness_from_end_date(query.period.end, today=as_of)
    return freshness_from_end_date(as_of, today=as_of)


def _query_for_limit(query: StatsguruQuery, limit: int) -> StatsguruQuery:
    if query.page is not None or query.size != DEFAULT_QUERY_STATS_LIMIT or limit <= query.size:
        return query
    for size in (100, 150, 200):
        if limit <= size:
            return query.model_copy(update={"size": size})
    return query.model_copy(update={"size": MAX_QUERY_STATS_LIMIT})


async def _fetch_limited_pages(
    call,
    query: StatsguruQuery,
    *,
    limit: int,
    as_of: date,
    freshness: Freshness,
    max_pages: int,
) -> tuple[ResultsPage, ...]:
    start_page = query.page or 1
    html = await call.fetch(query.results_url(as_of=as_of), freshness=freshness)
    first_page = parse_results_page(html)
    if first_page.no_records:
        return (first_page,)

    last_page = first_page.totals.pages or start_page
    last_needed = min(last_page, start_page + ceil(limit / query.size) - 1)
    pages_needed = last_needed - start_page + 1
    if pages_needed > max_pages:
        raise TooBroadError(
            f"That query is too broad: it needs {pages_needed} fetched pages "
            f"to return {limit} rows, but the limit is {max_pages}."
        )

    pages = [first_page]
    for current_page in range(start_page + 1, last_needed + 1):
        page_query = query.model_copy(update={"page": current_page})
        html = await call.fetch(page_query.results_url(as_of=as_of), freshness=freshness)
        pages.append(parse_results_page(html))
    return tuple(pages)


def _today(fetcher: Fetcher) -> date:
    return fetcher.clock.now().date()


def _display_columns(page: ResultsPage) -> tuple[str, ...]:
    return tuple(
        column
        for column in page.table.columns
        if column not in _METADATA_COLUMNS and not column.endswith("_not_out")
    )


def _display_row(row: Mapping[str, Any], columns: Iterable[str]) -> dict[str, Any]:
    return {column: _display_value(row, column) for column in columns}


def _display_value(row: Mapping[str, Any], column: str) -> Any:
    value = row.get(column)
    if value is None:
        return None
    rendered = _jsonable(value)
    if row.get(f"{column}_not_out") is True:
        return f"{rendered}*"
    return rendered


_FIELD_COLUMNS = {
    "batting": {
        "matches": "Mat",
        "innings": "Inns",
        "notouts": "NO",
        "outs": "Inns",
        "runs": "Runs",
        "high_score": "HS",
        "batting_average": "Ave",
        "balls_faced": "BF",
        "batting_strike_rate": "SR",
        "hundreds": "100",
        "fifty_plus": "50",
        "ducks": "0",
        "fours": "4s",
        "sixes": "6s",
    },
    "bowling": {
        "matches": "Mat",
        "innings_bowled": "Inns",
        "balls": "Balls",
        "overs": "Overs",
        "maidens": "Mdns",
        "conceded": "Runs",
        "wickets": "Wkts",
        "bbi": "BBI",
        "bbm": "BBM",
        "bowling_average": "Ave",
        "economy_rate": "Econ",
        "bowling_strike_rate": "SR",
        "four_plus_wickets": "4",
        "five_wickets": "5",
        "ten_wickets": "10",
    },
    "fielding": {
        "matches": "Mat",
        "innings_fielded": "Inns",
        "dismissals": "Dis",
        "caught": "Ct",
        "stumped": "St",
        "caught_keeper": "Ct Wk",
        "caught_fielder": "Ct Fi",
        "max_dismissals": "MD",
        "dismissals_per_inns": "D/I",
    },
    "allround": {
        "matches": "Mat",
        "runs": "Runs",
        "high_score": "HS",
        "batting_average": "Bat Av",
        "hundreds": "100",
        "wickets": "Wkts",
        "bbi": "BBI",
        "bbm": "BBM",
        "bowling_average": "Bowl Av",
        "five_wickets": "5",
        "caught": "Ct",
        "stumped": "St",
        "max_dismissals": "MD",
        "allround_average": "Ave Diff",
    },
    "fow": {
        "partners": "Partners",
        "start": "Span",
        "fow_innings": "Inns",
        "fow_notouts": "NO",
        "fow_runs": "Runs",
        "fow_high_score": "High",
        "fow_average": "Ave",
        "fow_hundreds": "100",
        "fow_fifty_plus": "50",
    },
    "team": {
        "team": "Team",
        "start": "Span",
        "matches": "Mat",
        "won": "Won",
        "lost": "Lost",
        "tied": "Tied",
        "drawn": "Draw",
        "no_result": "NR",
        "win_loss_ratio": "W/L",
        "runs": "Runs",
        "wickets": "Wkts",
        "balls": "Balls",
        "team_average": "Ave",
        "runs_per_over": "RPO",
        "team_innings": "Inns",
        "team_high_score": "HS",
        "team_low_score": "LS",
    },
    "aggregate": {
        "start": "Span",
        "matches": "Mat",
        "won": "Won",
        "tied": "Tied",
        "drawn": "Draw",
        "no_result": "NR",
        "runs": "Runs",
        "wickets": "Wkts",
        "balls": "Balls",
        "team_average": "Ave",
        "runs_per_over": "RPO",
    },
}


def _text_required_columns(query: StatsguruQuery) -> list[str]:
    fields = [query.orderby, *(qualification.field for qualification in query.qualifications)]
    columns = [_FIELD_COLUMNS[query.type].get(field or "", field or "") for field in fields]
    return [column for column in columns if column]


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Overs):
        return f"{value.overs}.{value.balls}" if value.balls else str(value.overs)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Span):
        return value.start if value.end is None else f"{value.start}-{value.end}"
    if is_dataclass(value):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def _reports_statsguru_cost(
    tool: Callable[..., Awaitable[CallToolResult]],
) -> Callable[..., Awaitable[CallToolResult]]:
    """Give each tool call a fresh tally, and report it on errors too.

    A ToolError becomes an error result with MCPServer's own text, plus the tally, since
    MCPServer's error results carry no `_meta`.
    """

    @functools.wraps(tool)
    async def call(*args: Any, **kwargs: Any) -> CallToolResult:
        start_request_tally()
        try:
            return await tool(*args, **kwargs)
        except ToolError as error:
            return CallToolResult(
                content=[
                    TextContent(type="text", text=f"Error executing tool {tool.__name__}: {error}")
                ],
                is_error=True,
                meta=_statsguru_meta(),
            )

    return call


def _statsguru_meta() -> dict[str, Any]:
    tally = request_tally()
    return {STATSGURU_META_KEY: {"requests": tally.requests, "cached_pages": tally.cached_pages}}


def _tool_result(
    summary: str,
    structured_content: Mapping[str, Any],
    *,
    text_required_columns: Iterable[str] = (),
    text: str | None = None,
) -> CallToolResult:
    return CallToolResult(
        content=[
            TextContent(
                type="text",
                text=(
                    _text_content(summary, structured_content, text_required_columns)
                    if text is None
                    else text
                ),
            )
        ],
        structured_content=_jsonable(dict(structured_content)),
        meta=_statsguru_meta(),
    )


def _text_content(
    summary: str, structured_content: Mapping[str, Any], text_required_columns: Iterable[str]
) -> str:
    answer_markdown = structured_content.get("answer_markdown")
    if isinstance(answer_markdown, str):
        return answer_markdown
    lines = [summary]
    link = structured_content.get("link")
    label = structured_content.get("label")
    if link is not None:
        lines.extend(["", f"Pinned link: [{label}]({link})"])
    candidates = structured_content.get("candidates")
    if isinstance(candidates, list) and candidates:
        headers = (
            ("Value", "Name", "Kind")
            if _has_lookup_candidates(candidates)
            else ("ID", "Name", "Country")
        )
        lines.extend(["", _markdown_table(headers, _candidate_rows(candidates))])
    rows = structured_content.get("rows")
    columns = structured_content.get("columns")
    if isinstance(rows, list) and rows and isinstance(columns, list):
        display_columns = _text_display_columns(
            [str(column) for column in columns],
            [str(column) for column in text_required_columns],
        )
        display_rows = [
            tuple(row.get(column) for column in display_columns)
            for row in rows[:10]
            if isinstance(row, Mapping)
        ]
        lines.extend(["", _markdown_table(display_columns, display_rows)])
        omitted_rows = max(0, len(rows) - len(display_rows))
        omitted_columns = max(0, len(columns) - len(display_columns))
        if omitted_rows or omitted_columns:
            lines.append(f"({omitted_rows} more row(s), {omitted_columns} more column(s) omitted.)")
    return "\n".join(lines)


def _text_display_columns(columns: list[str], required: list[str]) -> tuple[str, ...]:
    display = columns[:8]
    for column in required:
        if column in columns and column not in display:
            display.append(column)
    return tuple(display)


def _candidate_rows(candidates: list[Any]) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        nested = candidate.get("candidates")
        if isinstance(nested, list):
            kind = candidate.get("kind", "")
            rows.extend(
                (item.get("value"), item.get("name"), kind)
                for item in nested[:10]
                if isinstance(item, Mapping)
            )
            continue
        rows.append(
            (candidate.get("id"), candidate.get("name"), "/".join(candidate.get("country", [])))
        )
    return rows[:10]


def _has_lookup_candidates(candidates: list[Any]) -> bool:
    return any(
        isinstance(candidate, Mapping) and isinstance(candidate.get("candidates"), list)
        for candidate in candidates
    )


def _markdown_table(headers: tuple[str, ...], rows: Iterable[tuple[Any, ...]]) -> str:
    rendered_rows = [tuple("" if value is None else str(value) for value in row) for row in rows]
    widths = [len(header) for header in headers]
    for row in rendered_rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))
    header_line = (
        "| "
        + " | ".join(_pad(header, widths[index]) for index, header in enumerate(headers))
        + " |"
    )
    separator = "| " + " | ".join("-" * width for width in widths) + " |"
    body = [
        "| " + " | ".join(_pad(value, widths[index]) for index, value in enumerate(row)) + " |"
        for row in rendered_rows
    ]
    return "\n".join([header_line, separator, *body])


def _pad(value: str, width: int) -> str:
    return value + " " * (width - len(value))


def _validation_message(error: Exception) -> str:
    if isinstance(error, ValidationError):
        messages = []
        for item in error.errors():
            loc = ".".join(str(part) for part in item["loc"])
            received = f" (got {item['input']!r})" if "input" in item else ""
            messages.append(f"{loc}: {item['msg']}{received}" if loc else item["msg"])
        return "Invalid Statsguru query: " + "; ".join(messages)
    return f"Invalid Statsguru query: {error}"


def _progress_callback(ctx: Context | None):
    if ctx is None:
        return None
    progress = 0.0

    async def report(seconds: float, reason: str) -> None:
        nonlocal progress
        progress += seconds if seconds > 0 else 1
        if seconds > 0:
            message = f"{reason} for {seconds:g} seconds"
        else:
            message = reason
        await ctx.report_progress(progress, message=message)

    return report
