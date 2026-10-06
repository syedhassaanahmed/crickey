from __future__ import annotations

import re
import string
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from rapidfuzz import fuzz

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
_PUNCTUATION_TABLE = str.maketrans({char: " " for char in string.punctuation})
MAX_PLAYER_CANDIDATES = 5
COUNTRY_CODES = {
    "afghanistan": "AFG",
    "argentina": "ARG",
    "australia": "AUS",
    "austria": "AUT",
    "bahrain": "BHR",
    "bangladesh": "BAN",
    "bermuda": "BER",
    "canada": "CAN",
    "england": "ENG",
    "germany": "GER",
    "hong kong": "HKG",
    "india": "IND",
    "indonesia": "INA",
    "ireland": "IRE",
    "japan": "JPN",
    "kenya": "KENYA",
    "kuwait": "KUW",
    "malaysia": "MAS",
    "namibia": "NAM",
    "new zealand": "NZ",
    "nigeria": "NGA",
    "pakistan": "PAK",
    "south africa": "SA",
    "sri lanka": "SL",
    "west indies": "WI",
    "zimbabwe": "ZIM",
    "netherlands": "NED",
    "nepal": "NEP",
    "oman": "OMA",
    "papua new guinea": "PNG",
    "qatar": "QAT",
    "saudi arabia": "KSA",
    "scotland": "SCOT",
    "sierra leone": "SLE",
    "singapore": "SGP",
    "spain": "ESP",
    "united arab emirates": "UAE",
    "united states of america": "USA",
}
COUNTRY_CODE_VALUES = frozenset(code.casefold() for code in COUNTRY_CODES.values())


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
    note: str | None = None

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
            url = player_search_url(name)
            was_cached = self._fetcher.cache.get(url, self._fetcher.clock.monotonic()) is not None
            html = await fetch_call.fetch(url, freshness=Freshness.LOOKUP)
            result = _player_resolution_from_html(name, html, class_id=class_id, country=country)
            if result.match is not None or result.candidates or not was_cached:
                return result
            html = await fetch_call.fetch(url, freshness=Freshness.LOOKUP, force_refetch=True)
            return _player_resolution_from_html(name, html, class_id=class_id, country=country)

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


def _player_resolution_from_html(
    name: str, html: str, *, class_id: int, country: str | None
) -> PlayerResolution:
    try:
        rows = parse_player_search(html)
    except StatsguruParseError:
        return PlayerResolution(ResolveStatus.NEEDS_CLARIFICATION, name)
    all_candidates = tuple(
        PlayerCandidate(
            row.player_id,
            row.display_name,
            row.full_name,
            row.country_codes,
            tuple(fmt for fmt in row.formats if fmt.class_id == class_id and fmt.role == "player"),
        )
        for row in rows
        if _has_player_format(row.formats, class_id)
    )
    candidates, country_note = _filter_country(
        tuple(candidate for candidate in all_candidates if candidate.formats), country
    )
    candidates = tuple(candidate for candidate in candidates if candidate.formats)
    exact = tuple(
        candidate
        for candidate in candidates
        if _normalize(name) in {_normalize(candidate.name), _normalize(candidate.full_name or "")}
    )
    pool = _rank_player_candidates(name, exact or candidates)
    if len(pool) == 1 and country_note is None and _safe_auto_match(name, pool[0]):
        return PlayerResolution(ResolveStatus.MATCH, name, match=pool[0])
    return PlayerResolution(
        ResolveStatus.NEEDS_CLARIFICATION,
        name,
        candidates=pool[:MAX_PLAYER_CANDIDATES],
        note=country_note,
    )


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


def _filter_country(
    candidates: tuple[PlayerCandidate, ...], country: str | None
) -> tuple[tuple[PlayerCandidate, ...], str | None]:
    if country is None:
        return candidates, None
    codes = _country_codes(country)
    if not codes:
        return candidates, f"country {country!r} could not be applied"
    filtered = tuple(
        candidate
        for candidate in candidates
        if any(_normalize(code) in codes for code in candidate.country_codes)
    )
    if not filtered:
        return candidates, f"country {country!r} did not match any candidates"
    return filtered, None


def _country_codes(country: str) -> frozenset[str]:
    normalized_country = _normalize(country)
    if normalized_country in COUNTRY_CODES:
        return frozenset({_normalize(COUNTRY_CODES[normalized_country])})
    if normalized_country in COUNTRY_CODE_VALUES:
        return frozenset({normalized_country})
    return frozenset()


def _rank_player_candidates(
    query: str, candidates: tuple[PlayerCandidate, ...]
) -> tuple[PlayerCandidate, ...]:
    normalized_query = _normalize(query)

    def key(candidate: PlayerCandidate) -> tuple[int, int, int, float, str, int]:
        names = (_normalize(candidate.name), _normalize(candidate.full_name or ""))
        exact_rank = 0 if normalized_query in names else 1
        word_rank = 0 if any(normalized_query in name.split() for name in names) else 1
        relevance = max(fuzz.WRatio(normalized_query, name) for name in names if name)
        matches = max((fmt.match_count or 0 for fmt in candidate.formats), default=0)
        return (
            exact_rank,
            word_rank,
            -matches,
            -float(relevance),
            candidate.name,
            candidate.player_id,
        )

    return tuple(sorted(candidates, key=key))


def _safe_auto_match(query: str, candidate: PlayerCandidate) -> bool:
    query_words = _normalize(query).split()
    if not query_words:
        return False
    names = (_normalize(candidate.name), _normalize(candidate.full_name or ""))
    if any(name == " ".join(query_words) for name in names):
        return True
    return any(all(word in name.split() for word in query_words) for name in names)


def names_agree(given: str, fetched: str) -> bool:
    """Whether `given` could name the player Statsguru calls `fetched`.

    Only a clear clash counts, so surname-like tokens decide: the names agree when the last word
    of either appears in the other ("Imran Khan Niazi" and "Imran Khan", "Virat Kohli" and
    "V Kohli"), or when a one-word name starts with one of the fetched name's initials ("Virat"
    and "V Kohli").
    """
    given_words = _normalize(given).split()
    fetched_words = _normalize(fetched).split()
    if not given_words or not fetched_words:
        return True
    if given_words[-1] in fetched_words or fetched_words[-1] in given_words:
        return True
    initials = "".join(
        word for word in fetched.translate(_PUNCTUATION_TABLE).split() if _is_initials(word)
    ).casefold()
    return len(given_words) == 1 and given_words[0][0] in initials


def _is_initials(word: str) -> bool:
    return word.isalpha() and word.isupper() and len(word) <= 4


def _normalize(value: str) -> str:
    return _SPACE_RE.sub(" ", value.casefold().translate(_PUNCTUATION_TABLE)).strip()


def _parse_form_date(value: str) -> date:
    from crickey.parsers import parse_date

    return parse_date(value.replace("+", " "))
