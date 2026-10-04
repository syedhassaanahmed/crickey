from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, is_dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.context import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import CallToolResult, TextContent, ToolAnnotations
from pydantic import ValidationError

from crickey.fetcher import Fetcher, FetcherError, Freshness, freshness_from_end_date
from crickey.parsers import PlayerFormat, ResultsPage, Span, StatsguruParseError, parse_results_page
from crickey.query import QuerySpecError, ResolvedPeriod, StatsguruQuery
from crickey.render import freshness_line
from crickey.resolve import NameResolver, PlayerCandidate
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
        resolver = NameResolver(fetcher, budget=FIND_PLAYER_BUDGET_SECONDS)
        candidates: dict[int, PlayerCandidate] = {}
        progress = _progress_callback(ctx)
        try:
            async with fetcher.call(budget=FIND_PLAYER_BUDGET_SECONDS, progress=progress) as call:
                for class_id in class_ids:
                    resolution = await resolver.resolve_player(
                        name, class_id=class_id, country=country, call=call
                    )
                    found = resolution.candidates
                    if resolution.match is not None:
                        found = (*found, resolution.match)
                    for candidate in found:
                        candidates[candidate.player_id] = _merge_candidate(
                            candidates.get(candidate.player_id), candidate
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
        status = "match" if len(exact) == 1 and len(ordered) == 1 else "needs_clarification"
        if not ordered:
            status = "not_found"
        payload = {
            "status": status,
            "query": name,
            "candidates": [_candidate_payload(candidate) for candidate in ordered],
        }
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
        query: dict[str, Any],
        limit: int = DEFAULT_QUERY_STATS_LIMIT,
        fetch: bool = True,
        ctx: Context | None = None,
    ) -> CallToolResult:
        if limit < 1 or limit > MAX_QUERY_STATS_LIMIT:
            raise ToolError(f"limit must be from 1 to {MAX_QUERY_STATS_LIMIT}.")
        try:
            stats_query = StatsguruQuery.model_validate(query)
            as_of = _today(fetcher)
            url = stats_query.results_url(as_of=as_of)
            label = stats_query.label(as_of=as_of)
        except (ValidationError, QuerySpecError, ValueError) as error:
            raise ToolError(_validation_message(error)) from error

        payload: dict[str, Any] = {"link": url, "label": label, "fetch": fetch}
        if not fetch:
            payload.update({"columns": [], "rows": [], "total": None, "freshness": None})
            return _tool_result(f"Built Statsguru link without fetching: {label}", payload)

        progress = _progress_callback(ctx)
        try:
            async with fetcher.call(budget=FETCH_BUDGET_SECONDS, progress=progress) as call:
                pages = await call.fetch_pages(
                    url,
                    lambda page: stats_query.model_copy(update={"page": page}).results_url(
                        as_of=as_of
                    ),
                    freshness=_freshness_for_query(stats_query, as_of),
                )
        except FetcherError as error:
            raise ToolError(str(error)) from error

        try:
            parsed_pages = tuple(parse_results_page(page) for page in pages)
        except StatsguruParseError as error:
            raise ToolError(str(error)) from error

        first_page = parsed_pages[0]
        columns = _display_columns(first_page)
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
                "total": first_page.totals.total,
                "page_count": first_page.totals.pages,
                "freshness": freshness,
            }
        )
        return _tool_result(
            f"Fetched {len(rows)} row(s) of {first_page.totals.total} for {label}. {freshness}",
            payload,
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


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
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


def _tool_result(summary: str, structured_content: Mapping[str, Any]) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=summary)],
        structured_content=_jsonable(dict(structured_content)),
    )


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

    async def report(seconds: float, reason: str) -> None:
        if seconds > 0:
            message = f"{reason} for {seconds:g} seconds"
        else:
            message = reason
        await ctx.report_progress(0, message=message)

    return report
