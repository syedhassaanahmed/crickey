from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, is_dataclass
from datetime import date
from decimal import Decimal
from math import ceil
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.context import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import CallToolResult, TextContent, ToolAnnotations
from pydantic import ValidationError

from crickey.fetcher import Fetcher, FetcherError, Freshness, TooBroadError, freshness_from_end_date
from crickey.parsers import (
    Overs,
    PlayerFormat,
    ResultsPage,
    Span,
    StatsguruParseError,
    parse_results_page,
)
from crickey.query import QuerySpecError, ResolvedPeriod, StatsguruQuery, player_search_url
from crickey.render import freshness_line
from crickey.resolve import PlayerCandidate
from crickey.resolve import _country_codes as country_codes
from crickey.resolve import _normalize as normalize_country
from crickey.resolve import _player_resolution_from_html as player_resolution_from_html
from crickey.settings import Settings

SERVER_INSTRUCTIONS = """\
Prefer crickey's answer tools and show answer_markdown as-is when they return it.
Do not do multi-row arithmetic yourself; use crickey tools for comparisons and derived rates.
Cite only links that came from crickey tools.
Read "T20" as T20I unless a domestic or franchise league is named."""

FETCH_BUDGET_SECONDS = 240.0
FIND_PLAYER_BUDGET_SECONDS = 60.0
MAX_QUERY_STATS_LIMIT = 200
DEFAULT_QUERY_STATS_LIMIT = 50

_READ_ONLY_ANNOTATIONS = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
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
    "test/odi/t20i": 11,
    "combined internationals": 11,
}
_DEFAULT_FIND_CLASSES = (1, 2, 3, 6, 11)
_METADATA_COLUMNS = {"player_name", "player_id", "player_team_codes", "match_id"}


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
            "Example: Which players have scored Test hundreds more frequently than "
            "Babar Azam? Find Statsguru player candidates by name, with player ID, "
            "country, formats and career spans."
        ),
    )
    async def find_player(
        name: str,
        format: str | int | None = None,
        country: str | None = None,
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
            "Example: How many hundreds has Babar Azam scored in ODI World Cups? "
            "Compile and optionally fetch any Statsguru query, returning rows, "
            "totals and the pinned link."
        ),
    )
    async def query_stats(
        query: StatsguruQuery,
        limit: int = DEFAULT_QUERY_STATS_LIMIT,
        fetch: bool = True,
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

    return mcp


def _format_class(value: str | int) -> int:
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
        resolution = player_resolution_from_html(name, html, class_id=class_id, country=None)
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
    "matches": "Mat",
    "innings": "Inns",
    "notouts": "NO",
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
    "overs": "Overs",
    "wickets": "Wkts",
    "bowling_average": "Ave",
    "economy_rate": "Econ",
}


def _text_required_columns(query: StatsguruQuery) -> list[str]:
    fields = [query.orderby, *(qualification.field for qualification in query.qualifications)]
    columns = [_FIELD_COLUMNS.get(field or "", field or "") for field in fields]
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


def _tool_result(
    summary: str,
    structured_content: Mapping[str, Any],
    *,
    text_required_columns: Iterable[str] = (),
) -> CallToolResult:
    return CallToolResult(
        content=[
            TextContent(
                type="text",
                text=_text_content(summary, structured_content, text_required_columns),
            )
        ],
        structured_content=_jsonable(dict(structured_content)),
    )


def _text_content(
    summary: str, structured_content: Mapping[str, Any], text_required_columns: Iterable[str]
) -> str:
    lines = [summary]
    link = structured_content.get("link")
    label = structured_content.get("label")
    if link is not None:
        lines.extend(["", f"Pinned link: [{label}]({link})"])
    candidates = structured_content.get("candidates")
    if isinstance(candidates, list) and candidates:
        lines.extend(["", _markdown_table(("ID", "Name", "Country"), _candidate_rows(candidates))])
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
    return [
        (candidate.get("id"), candidate.get("name"), "/".join(candidate.get("country", [])))
        for candidate in candidates[:10]
        if isinstance(candidate, Mapping)
    ]


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
