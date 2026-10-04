from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from crickey.fetcher import Fetcher, Freshness
from crickey.ids import LookupResult, StatsguruIdResolver, lookup_team, lookup_trophy
from crickey.parsers import (
    PlayerFormat,
    StatsguruParseError,
    parse_filter_form,
    parse_player_page,
    parse_player_search,
)
from crickey.query import PlayerPageSpec, ResolvedPeriod, SymbolicPeriod, player_search_url

_PLAYER_FORM_URL = (
    "https://stats.cricinfo.com/ci/engine/player/{player_id}.html?class={class_id};type=batting"
)
_SPACE_RE = re.compile(r"\s+")


class ResolveStatus(StrEnum):
    MATCH = "match"
    NEEDS_CLARIFICATION = "needs_clarification"


@dataclass(frozen=True)
class PlayerCandidate:
    player_id: int
    name: str
    full_name: str | None
    country_codes: tuple[str, ...]
    formats: tuple[PlayerFormat, ...]


@dataclass(frozen=True)
class PlayerResolution:
    status: ResolveStatus
    query: str
    match: PlayerCandidate | None = None
    candidates: tuple[PlayerCandidate, ...] = ()

    @property
    def needs_clarification(self) -> bool:
        return self.status == ResolveStatus.NEEDS_CLARIFICATION


class NameResolver:
    def __init__(self, fetcher: Fetcher, *, budget: float = 60.0) -> None:
        self._fetcher = fetcher
        self._budget = budget
        self._id_resolver = StatsguruIdResolver(fetcher, budget=budget)

    async def resolve_player(
        self,
        name: str,
        *,
        class_id: int,
        country: str | None = None,
        call=None,
    ) -> PlayerResolution:
        async def _resolve(fetch_call) -> PlayerResolution:
            html = await fetch_call.fetch(player_search_url(name), freshness=Freshness.LOOKUP)
            try:
                rows = parse_player_search(html)
            except StatsguruParseError:
                return PlayerResolution(ResolveStatus.NEEDS_CLARIFICATION, name)
            candidates = tuple(
                PlayerCandidate(
                    row.player_id,
                    row.display_name,
                    row.full_name,
                    row.country_codes,
                    tuple(
                        fmt
                        for fmt in row.formats
                        if fmt.class_id == class_id and fmt.role == "player"
                    ),
                )
                for row in rows
                if _has_player_format(row.formats, class_id)
                and _country_matches(row.country_codes, country)
            )
            candidates = tuple(candidate for candidate in candidates if candidate.formats)
            exact = tuple(
                candidate
                for candidate in candidates
                if _normalize(name)
                in {_normalize(candidate.name), _normalize(candidate.full_name or "")}
            )
            pool = exact or candidates
            if len(pool) == 1:
                return PlayerResolution(ResolveStatus.MATCH, name, match=pool[0])
            return PlayerResolution(ResolveStatus.NEEDS_CLARIFICATION, name, candidates=pool[:5])

        if call is not None:
            return await _resolve(call)
        async with self._fetcher.call(budget=self._budget) as fetch_call:
            return await _resolve(fetch_call)

    def resolve_team(self, name: str, *, class_id: int) -> LookupResult:
        return lookup_team(class_id, name)

    def resolve_trophy(self, name: str, *, class_id: int) -> LookupResult:
        return lookup_trophy(class_id, name)

    async def resolve_ground(self, name: str, *, class_id: int, call=None) -> LookupResult:
        return await self._id_resolver.lookup_ground(class_id, name, call=call)


class PeriodResolver:
    def __init__(self, fetcher: Fetcher, *, budget: float = 60.0) -> None:
        self._fetcher = fetcher
        self._budget = budget

    async def career_span(self, player_id: int, *, class_id: int, call=None) -> ResolvedPeriod:
        async def _resolve(fetch_call) -> ResolvedPeriod:
            form_url = _PLAYER_FORM_URL.format(player_id=player_id, class_id=class_id)
            html = await fetch_call.fetch(form_url, freshness=Freshness.LOOKUP)
            try:
                form = parse_filter_form(html)
                start = _parse_form_date(form.hidden_fields["spanmin0"])
                end = _parse_form_date(form.hidden_fields["spanmax0"])
                return ResolvedPeriod(start=start, end=end)
            except KeyError, StatsguruParseError, ValueError:
                page_url = PlayerPageSpec(
                    player_id=player_id,
                    **{"class": class_id, "type": "batting", "view": "innings"},
                ).url()
                innings_html = await fetch_call.fetch(page_url, freshness=Freshness.RECENT)
                page = parse_player_page(innings_html)
                if page.innings is None or "Start Date" not in page.innings:
                    raise StatsguruParseError(
                        "player innings list is needed to resolve career span"
                    ) from None
                dates = [value for value in page.innings["Start Date"] if isinstance(value, date)]
                if not dates:
                    raise StatsguruParseError("player innings list has no start dates") from None
                return ResolvedPeriod(start=min(dates), end=max(dates))

        if call is not None:
            return await _resolve(call)
        async with self._fetcher.call(budget=self._budget) as fetch_call:
            return await _resolve(fetch_call)

    async def resolve_symbolic(
        self, player_id: int, *, class_id: int, period: SymbolicPeriod, call=None
    ) -> ResolvedPeriod:
        career = await self.career_span(player_id, class_id=class_id, call=call)
        return period.resolve(career.start, career.end)


def profile_url(player_id: int) -> str:
    return f"https://stats.cricinfo.com/ci/content/player/{player_id}.html"


def _has_player_format(formats: tuple[PlayerFormat, ...], class_id: int) -> bool:
    return any(fmt.class_id == class_id and fmt.role == "player" for fmt in formats)


def _country_matches(countries: tuple[str, ...], country: str | None) -> bool:
    if country is None:
        return True
    wanted = _normalize(country)
    return any(_normalize(code) == wanted for code in countries)


def _normalize(value: str) -> str:
    return _SPACE_RE.sub(" ", value.casefold()).strip()


def _parse_form_date(value: str) -> date:
    from crickey.parsers import parse_date

    return parse_date(value.replace("+", " "))
