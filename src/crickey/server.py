from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, is_dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from math import ceil
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.context import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, PositiveInt, ValidationError

from crickey.fetcher import Fetcher, FetcherError, Freshness, TooBroadError, freshness_from_end_date
from crickey.ids import LookupResult, lookup_host
from crickey.metrics import Metric, batting_metric, rank_key
from crickey.parsers import (
    Overs,
    PlayerFormat,
    ResultsPage,
    Span,
    StatsguruParseError,
    parse_current_or_recent_matches,
    parse_player_page,
    parse_results_page,
)
from crickey.proof import ProofLink, Threshold, build_proof_link
from crickey.query import (
    Period,
    PlayerPageSpec,
    Qualification,
    QuerySpecError,
    ResolvedPeriod,
    SeasonPeriod,
    StatsguruQuery,
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
from crickey.resolve import NameResolver, PeriodResolver, PlayerCandidate
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


class AnswerMetric(StrEnum):
    RUNS = "runs"
    AVERAGE = "average"
    STRIKE_RATE = "strike_rate"
    HUNDREDS = "hundreds"
    FIFTIES = "fifties"
    INNINGS_PER_HUNDRED = "innings_per_hundred"
    INNINGS_PER_FIFTY_PLUS = "innings_per_fifty_plus"
    BALLS_PER_DISMISSAL = "balls_per_dismissal"


class AllTimePeriod(BaseModel):
    kind: Literal["all_time"] = "all_time"


class CareerPeriod(BaseModel):
    kind: Literal["career"] = "career"


class FirstYearsPeriod(BaseModel):
    kind: Literal["first_years"] = "first_years"
    years: PositiveInt


class LastYearsPeriod(BaseModel):
    kind: Literal["last_years"] = "last_years"
    years: PositiveInt


class DatesPeriod(BaseModel):
    kind: Literal["dates"] = "dates"
    start: date
    end: date


class AnswerSeasonPeriod(BaseModel):
    kind: Literal["season"] = "season"
    season: str


AnswerPeriod = (
    AllTimePeriod
    | CareerPeriod
    | FirstYearsPeriod
    | LastYearsPeriod
    | DatesPeriod
    | AnswerSeasonPeriod
)


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

    @mcp.tool(
        annotations=_READ_ONLY_ANNOTATIONS,
        description=(
            "Example: Average number of innings taken per ODI century (minimum X number "
            "of centuries). Return a batting leaderboard with proof links."
        ),
    )
    async def leaderboard(
        format: str | int,
        metric: AnswerMetric | str,
        period: AnswerPeriod | None = None,
        team: str | None = None,
        opposition: str | None = None,
        host_country: str | None = None,
        ground: str | None = None,
        trophy: str | None = None,
        home_or_away: str | int | None = None,
        match_result: str | int | None = None,
        minimum: int | Decimal | None = None,
        top_n: int = 10,
        ctx: Context | None = None,
    ) -> CallToolResult:
        return await _leaderboard_tool(
            fetcher,
            fetcher.settings,
            format=format,
            metric_key=metric,
            period=period,
            team=team,
            opposition=opposition,
            host_country=host_country,
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
            "Example: Which batters had better average and strike rate in T20 than "
            "Babar Azam, in the same period that Babar Azam played? Compare players."
        ),
    )
    async def better_than_player(
        player_name: str,
        format: str | int,
        metrics: list[AnswerMetric | str],
        match: str = "all",
        period: AnswerPeriod | None = None,
        team: str | None = None,
        opposition: str | None = None,
        host_country: str | None = None,
        ground: str | None = None,
        trophy: str | None = None,
        home_or_away: str | int | None = None,
        match_result: str | int | None = None,
        minimum: int | Decimal | None = None,
        ctx: Context | None = None,
    ) -> CallToolResult:
        return await _better_than_player_tool(
            fetcher,
            fetcher.settings,
            player_name=player_name,
            format=format,
            metric_keys=metrics,
            match_mode=match,
            period=period,
            team=team,
            opposition=opposition,
            host_country=host_country,
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
            "Example: What was Babar Azam's Test batting average in the last Y years "
            "of his career? Return one player's batting record with a proof link."
        ),
    )
    async def player_record(
        player_name: str,
        format: str | int,
        period: AnswerPeriod | None = None,
        opposition: str | None = None,
        host_country: str | None = None,
        ground: str | None = None,
        trophy: str | None = None,
        home_or_away: str | int | None = None,
        match_result: str | int | None = None,
        ctx: Context | None = None,
    ) -> CallToolResult:
        return await _player_record_tool(
            fetcher,
            player_name=player_name,
            format=format,
            period=period,
            opposition=opposition,
            host_country=host_country,
            ground=ground,
            trophy=trophy,
            home_or_away=home_or_away,
            match_result=match_result,
            ctx=ctx,
        )

    return mcp


async def _leaderboard_tool(
    fetcher: Fetcher,
    settings: Settings,
    *,
    format: str | int,
    metric_key: str,
    period: AnswerPeriod | None,
    team: str | None,
    opposition: str | None,
    host_country: str | None,
    ground: str | None,
    trophy: str | None,
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
        metric = _answer_metric(metric_key)
        metric.require_supported(class_id)
        minimum_value = minimum if minimum is not None else metric.default_minimum(class_id).minimum
        minimum_field = metric.default_minimum(class_id).field
        resolved_period = _parse_period(period)
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
                ground=ground,
                trophy=trophy,
                home_or_away=home_or_away,
                match_result=match_result,
            )
            if filters.get("needs_clarification"):
                return _tool_result("A filter needs clarification.", filters)
            query = StatsguruQuery(
                **{
                    "class": class_id,
                    "type": "batting",
                    "period": resolved_period,
                    "qualifications": (Qualification(field=minimum_field, minimum=minimum_value),),
                    "orderby": metric.orderby or minimum_field,
                    "orderbyad": "reverse" if metric.direction.value == "lower" else "",
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
    answer = render_answer(
        AnswerRenderInput(
            short_answer=_leaderboard_short_answer(metric, payload_rows, group_value),
            table=RenderedTable(
                headers=("Rank", "Player", metric.label, minimum_field),
                rows=tuple(
                    (row["rank"], row["player"], row["value"], row.get(minimum_field))
                    for row in payload_rows
                ),
            ),
            method=(
                (
                    "Fetched every qualifying Statsguru row within the "
                    f"{settings.max_pages}-page limit."
                ),
                "Calculated derived metrics from totals."
                if metric.is_derived
                else "Used Statsguru displayed values.",
                "Equal displayed values count as ties.",
            ),
            assumptions=(
                f"Minimum: {minimum_field} >= {minimum_value}.",
                f"Period: {_period_text(resolved_period, as_of=as_of)}.",
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


async def _better_than_player_tool(
    fetcher: Fetcher,
    settings: Settings,
    *,
    player_name: str,
    format: str | int,
    metric_keys: list[AnswerMetric | str],
    match_mode: str,
    period: AnswerPeriod | None,
    team: str | None,
    opposition: str | None,
    host_country: str | None,
    ground: str | None,
    trophy: str | None,
    home_or_away: str | int | None,
    match_result: str | int | None,
    minimum: int | Decimal | None,
    ctx: Context | None,
) -> CallToolResult:
    if not 1 <= len(metric_keys) <= 3:
        raise ToolError("metrics must contain 1 to 3 batting metrics.")
    if match_mode not in {"all", "any"}:
        raise ToolError("match must be 'all' or 'any'.")
    try:
        class_id = _format_class(format)
        metrics = tuple(_answer_metric(key) for key in metric_keys)
        for metric in metrics:
            metric.require_supported(class_id)
    except ValueError as error:
        raise ToolError(str(error)) from error
    progress = _progress_callback(ctx)
    try:
        async with fetcher.call(budget=FETCH_BUDGET_SECONDS, progress=progress) as call:
            names = NameResolver(fetcher)
            resolution = await names.resolve_player(player_name, class_id=class_id, call=call)
            if resolution.needs_clarification or resolution.match is None:
                return _clarification_result(player_name, resolution)
            period_value = await _comparison_period(
                fetcher, resolution.match.player_id, class_id, period, call
            )
            filters = await _answer_filters(
                fetcher,
                class_id,
                call=call,
                team=team,
                opposition=opposition,
                host_country=host_country,
                ground=ground,
                trophy=trophy,
                home_or_away=home_or_away,
                match_result=match_result,
            )
            if filters.get("needs_clarification"):
                return _tool_result("A filter needs clarification.", filters)
            min_field, min_value = _comparison_default_minimum(metrics, class_id)
            query = StatsguruQuery(
                **{
                    "class": class_id,
                    "type": "batting",
                    "period": period_value,
                    "qualifications": (
                        Qualification(
                            field=min_field,
                            minimum=minimum if minimum is not None else min_value,
                        ),
                    ),
                    "orderby": metrics[0].orderby,
                    "size": 200,
                    **filters["query"],
                }
            )
            pages = await _fetch_all_result_pages(call, query, fetcher, settings)
            all_rows = [row for page in pages for row in page.table.to_dict("records")]
            target = next(
                (row for row in all_rows if _row_id(row) == resolution.match.player_id), None
            )
            if target is None:
                raise ToolError(
                    f"{resolution.match.name} was not in the qualifying Statsguru rows."
                )
            target_values = tuple(
                metric.display_value(metric.value_from_row(target, class_id=class_id))
                for metric in metrics
            )
            winners = []
            for row in all_rows:
                comparisons = [
                    metric.compare(metric.value_from_row(row, class_id=class_id), target_value)
                    for metric, target_value in zip(metrics, target_values, strict=True)
                ]
                if _row_id(row) == resolution.match.player_id:
                    winners.append(row)
                    continue
                beats = (
                    all(value > 0 for value in comparisons)
                    if match_mode == "all"
                    else any(value > 0 for value in comparisons)
                )
                if beats:
                    winners.append(row)
            winners = sorted(
                winners,
                key=lambda row: tuple(
                    rank_key(metric, metric.value_from_row(row, class_id=class_id))
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
                    _row_id(row) for row in winners if _row_id(row) is not None
                ),
                call=call,
                as_of=_today(fetcher),
                page_allowance=max(0, settings.max_pages - len(pages)),
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
        _comparison_row_payload(row, metrics, class_id, resolution.match.player_id)
        for row in winners
    ]
    as_of = _today(fetcher)
    metric_labels = tuple(metric.label for metric in metrics)
    beater_count = len([row for row in payload_rows if row["relation"] == "beats"])
    metrics_text = " and ".join(metric_labels)
    answer = render_answer(
        AnswerRenderInput(
            short_answer=(
                f"{beater_count} players matched {resolution.match.name}'s "
                f"displayed {metrics_text}."
            ),
            table=RenderedTable(
                headers=("Player", *metric_labels, "Relation"),
                rows=tuple(
                    (row["player"], *(row["values"][m.key] for m in metrics), row["relation"])
                    for row in payload_rows
                ),
            ),
            method=(
                "Compared Statsguru displayed values; equal displayed values count as ties.",
                (
                    f"Required {'all' if match_mode == 'all' else 'any'} metric(s) "
                    "to match or beat the player."
                ),
            ),
            assumptions=(
                f"Minimum: {query.qualifications[0].field} >= {query.qualifications[0].minimum}.",
                f"Period: {_period_text(period_value, as_of=as_of)}.",
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
            "player": _candidate_payload(resolution.match),
            "metrics": [metric.key for metric in metrics],
            "rows": payload_rows,
            "beaters": [row for row in payload_rows if row["relation"] == "beats"],
            "proof": _jsonable(proof),
            "request_pages": len(pages),
        },
    )


async def _player_record_tool(
    fetcher: Fetcher,
    *,
    player_name: str,
    format: str | int,
    period: AnswerPeriod | None,
    opposition: str | None,
    host_country: str | None,
    ground: str | None,
    trophy: str | None,
    home_or_away: str | int | None,
    match_result: str | int | None,
    ctx: Context | None,
) -> CallToolResult:
    try:
        class_id = _format_class(format)
    except ValueError as error:
        raise ToolError(str(error)) from error
    progress = _progress_callback(ctx)
    try:
        async with fetcher.call(budget=FETCH_BUDGET_SECONDS, progress=progress) as call:
            names = NameResolver(fetcher)
            resolution = await names.resolve_player(player_name, class_id=class_id, call=call)
            if resolution.needs_clarification or resolution.match is None:
                return _clarification_result(player_name, resolution)
            period_value = await _record_period(
                fetcher, resolution.match.player_id, class_id, period, call
            )
            filters = await _answer_filters(
                fetcher,
                class_id,
                call=call,
                opposition=opposition,
                host_country=host_country,
                ground=ground,
                trophy=trophy,
                home_or_away=home_or_away,
                match_result=match_result,
            )
            if filters.get("needs_clarification"):
                return _tool_result("A filter needs clarification.", filters)
            spec = PlayerPageSpec(
                player_id=resolution.match.player_id,
                **{
                    "class": class_id,
                    "type": "batting",
                    "period": period_value,
                    **filters["player_page"],
                },
            )
            as_of = _today(fetcher)
            url = spec.url(as_of=as_of)
            html = await call.fetch(
                url,
                freshness=_freshness_for_query(
                    StatsguruQuery(
                        **{"class": class_id, "type": "batting", "period": period_value}
                    ),
                    as_of,
                ),
            )
            page = parse_player_page(html)
            row = _player_record_row(page)
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

    columns = _player_record_columns(page)
    values = {column: _display_value(row, column) for column in columns}
    proof = ProofLink(spec.label(as_of=as_of), url, True, True, row_count=len(page.career_averages))
    summary_values = _player_record_summary(resolution.match.name, values)
    answer = render_answer(
        AnswerRenderInput(
            short_answer=summary_values,
            table=RenderedTable(
                headers=tuple(columns), rows=(tuple(values[column] for column in columns),)
            ),
            method=("Read the player's Statsguru batting page with the same filters.",),
            assumptions=(f"Period: {_period_text(period_value, as_of=as_of)}.",),
            proof_links=(proof,),
            players=(RenderPlayer(resolution.match.name, resolution.match.player_id),),
            as_of=as_of,
            current_or_recent_matches=recent,
        )
    )
    return _tool_result(
        "Built player record.",
        {
            "status": "ok",
            "answer_markdown": answer,
            "player": _candidate_payload(resolution.match),
            "row": values,
            "proof": _jsonable(proof),
        },
    )


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


def _answer_metric(value: AnswerMetric | str) -> Metric:
    normalized = str(value.value if isinstance(value, AnswerMetric) else value)
    normalized = " ".join(normalized.casefold().replace("_", " ").replace("-", " ").split())
    for metric in (batting_metric(key.value) for key in AnswerMetric):
        labels = {
            metric.key.casefold().replace("_", " "),
            metric.label.casefold(),
        }
        if normalized in labels:
            return metric
    valid = ", ".join(metric.value for metric in AnswerMetric)
    labels = ", ".join(batting_metric(metric.value).label for metric in AnswerMetric)
    raise ValueError(f"unknown batting metric {value!r}; valid keys: {valid}; labels: {labels}")


async def _answer_filters(
    fetcher: Fetcher,
    class_id: int,
    *,
    call,
    team: str | None = None,
    opposition: str | None = None,
    host_country: str | None = None,
    ground: str | None = None,
    trophy: str | None = None,
    home_or_away: str | int | None = None,
    match_result: str | int | None = None,
) -> dict[str, Any]:
    resolver = NameResolver(fetcher)
    query: dict[str, Any] = {}
    player_page: dict[str, Any] = {}
    clarifications: list[dict[str, Any]] = []

    def add_lookup(field: str, result: LookupResult, *, for_player_page: bool = True) -> None:
        if result.match is not None:
            query[field] = result.match.value
            if for_player_page:
                player_page[field] = result.match.value
            return
        clarifications.append(_lookup_payload(result))

    if team is not None:
        add_lookup("team", resolver.resolve_team(team, class_id=class_id), for_player_page=False)
    if opposition is not None:
        add_lookup("opposition", resolver.resolve_team(opposition, class_id=class_id))
    if host_country is not None:
        add_lookup("host", lookup_host(class_id, host_country))
    if trophy is not None:
        add_lookup("trophy", resolver.resolve_trophy(trophy, class_id=class_id))
    if ground is not None:
        add_lookup("ground", await resolver.resolve_ground(ground, class_id=class_id, call=call))
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


def _parse_period(value: AnswerPeriod | Mapping[str, Any] | None) -> Period:
    if value is None:
        return None
    data = value.model_dump() if isinstance(value, BaseModel) else dict(value)
    if not data or data == {"kind": "all_time"}:
        return None
    kind = data.get("kind")
    if kind in {"dates", "date_range"}:
        return ResolvedPeriod(start=_date_value(data["start"]), end=_date_value(data["end"]))
    if kind == "season":
        return SeasonPeriod(season=str(data["season"]))
    if kind in {"career", "first_years", "last_years"}:
        years = data.get("years")
        return SymbolicPeriod(kind=SymbolicPeriodKind(kind), years=years)
    raise QuerySpecError(f"unsupported period kind {kind!r}")


async def _comparison_period(
    fetcher: Fetcher, player_id: int, class_id: int, value: AnswerPeriod | None, call
) -> Period:
    period = _parse_period(value or CareerPeriod())
    if isinstance(period, SymbolicPeriod):
        return await PeriodResolver(fetcher).resolve_symbolic(
            player_id, class_id=class_id, period=period, call=call
        )
    return period


async def _record_period(
    fetcher: Fetcher, player_id: int, class_id: int, value: AnswerPeriod | None, call
) -> Period:
    period = _parse_period(value)
    if isinstance(period, SymbolicPeriod):
        return await PeriodResolver(fetcher).resolve_symbolic(
            player_id, class_id=class_id, period=period, call=call
        )
    return period


def _date_value(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


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


def _comparison_default_minimum(
    metrics: tuple[Metric, ...], class_id: int
) -> tuple[str, int | Decimal]:
    if any(metric.key in {"average", "strike_rate"} for metric in metrics):
        minimum = batting_metric("runs").default_minimum(class_id)
        return minimum.field, max(Decimal(minimum.minimum), Decimal(1000))
    minimum = metrics[0].default_minimum(class_id)
    return minimum.field, minimum.minimum


async def _fetch_all_result_pages(
    call,
    query: StatsguruQuery,
    fetcher: Fetcher,
    settings: Settings,
) -> tuple[ResultsPage, ...]:
    as_of = _today(fetcher)
    pages = [
        parse_results_page(
            await call.fetch(
                query.results_url(as_of=as_of), freshness=_freshness_for_query(query, as_of)
            )
        )
    ]
    total_pages = pages[0].totals.pages or 1
    if total_pages > settings.max_pages:
        raise TooBroadError(
            f"That query is too broad: it needs {total_pages} pages, "
            f"but the limit is {settings.max_pages}."
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
    for field in ("Inns", "100", "50", "Runs", "Ave", "SR", "BF"):
        if field in row:
            payload[field] = _display_value(row, field)
    for qual_field, column in {
        "hundreds": "100",
        "innings": "Inns",
        "runs": "Runs",
        "balls_faced": "BF",
        "fifty_plus": "50",
    }.items():
        if column in row:
            payload[qual_field] = _display_value(row, column)
    return payload


def _comparison_row_payload(
    row: Mapping[str, Any], metrics: tuple[Metric, ...], class_id: int, target_id: int
) -> dict[str, Any]:
    return {
        "player": row.get("player_name") or row.get("Player"),
        "player_id": _row_id(row),
        "relation": "target" if _row_id(row) == target_id else "beats",
        "values": {
            metric.key: metric.display_value(metric.value_from_row(row, class_id=class_id))
            for metric in metrics
        },
    }


def _player_record_row(page) -> Mapping[str, Any]:
    rows = page.career_averages.to_dict("records")
    filtered = [row for row in rows if str(row.get("Grouping", "")).casefold() == "filtered"]
    return filtered[0] if filtered else rows[-1]


def _player_record_columns(page) -> list[str]:
    return [
        column
        for column in page.career_averages.columns
        if column not in _METADATA_COLUMNS and not column.endswith("_not_out")
    ]


def _player_record_summary(player_name: str, values: Mapping[str, Any]) -> str:
    labels = (
        ("Mat", "matches"),
        ("Inns", "innings"),
        ("Runs", "runs"),
        ("Ave", "average"),
        ("100", "hundreds"),
        ("50", "fifties"),
    )
    pieces = [f"{label} {values[column]}" for column, label in labels if column in values]
    if "HS" in values:
        pieces.append(f"highest score {values['HS']}")
    return f"{player_name}'s record: {', '.join(pieces)}."


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


def _clarification_result(name: str, resolution) -> CallToolResult:
    return _tool_result(
        f"Found possible player candidates for {name!r}; please choose one.",
        {
            "status": "needs_clarification",
            "query": name,
            "candidates": [_candidate_payload(candidate) for candidate in resolution.candidates],
        },
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
