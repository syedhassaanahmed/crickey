from __future__ import annotations

import re
import string
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import quote_plus

from rapidfuzz import fuzz

from crickey import id_tables
from crickey.fetcher import Fetcher, Freshness
from crickey.parsers import FormOption, parse_filter_form

FORM_URL = "https://stats.cricinfo.com/ci/engine/stats/index.html?class={class_id};filter=advanced;type=batting"
INVOLVE_URL = (
    "https://stats.cricinfo.com/ci/engine/stats/index.html?"
    "class={class_id};filter=advanced;{search_field}={query};type=batting"
)

# Fuzzy matching policy: after case, whitespace and punctuation normalization, exact matches win.
# Otherwise WRatio >= 90 is accepted only when it is at least three points ahead of the
# next candidate. Scores >= 75 are useful clarification candidates; if nothing reaches
# that bar, the nearest names are still returned so callers can ask the user to clarify.
FUZZY_ACCEPT_SCORE = 90.0
FUZZY_ACCEPT_MARGIN = 3.0
FUZZY_CANDIDATE_SCORE = 75.0
MAX_CANDIDATES = 5

_PUNCTUATION_TABLE = str.maketrans({char: " " for char in string.punctuation})
_SPACE_RE = re.compile(r"\s+")
_INVOLVE_LABEL_RE = re.compile(r'^found using "[^"]+":\s*(?P<name>.+)$')


class LookupStatus(StrEnum):
    MATCH = "match"
    NEEDS_CLARIFICATION = "needs_clarification"


@dataclass(frozen=True)
class IdCandidate:
    class_id: int
    kind: str
    value: int
    name: str
    score: float | None = None


@dataclass(frozen=True)
class LookupResult:
    status: LookupStatus
    query: str
    class_id: int
    kind: str
    match: IdCandidate | None = None
    candidates: tuple[IdCandidate, ...] = ()

    @property
    def needs_clarification(self) -> bool:
        return self.status == LookupStatus.NEEDS_CLARIFICATION


def lookup_team(class_id: int, name: str) -> LookupResult:
    return resolve_name(class_id, "team", name, _class_table(id_tables.TEAMS, class_id, "team"))


def lookup_host(class_id: int, name: str) -> LookupResult:
    return resolve_name(class_id, "host", name, _class_table(id_tables.HOSTS, class_id, "host"))


def lookup_continent(class_id: int, name: str) -> LookupResult:
    return resolve_name(
        class_id, "continent", name, _class_table(id_tables.CONTINENTS, class_id, "continent")
    )


def lookup_trophy(class_id: int, name: str) -> LookupResult:
    return resolve_name(
        class_id, "trophy", name, _class_table(id_tables.TROPHIES, class_id, "trophy")
    )


def resolve_name(
    class_id: int,
    kind: str,
    query: str,
    table: Mapping[int, str],
) -> LookupResult:
    if not table:
        raise ValueError(f"{kind} table for class {class_id} is empty")
    normalized_query = _normalize(query)
    rows = tuple(IdCandidate(class_id, kind, value, label) for value, label in table.items())
    exact = tuple(candidate for candidate in rows if _normalize(candidate.name) == normalized_query)
    if len(exact) == 1:
        return LookupResult(LookupStatus.MATCH, query, class_id, kind, match=exact[0])
    if len(exact) > 1:
        return LookupResult(
            LookupStatus.NEEDS_CLARIFICATION, query, class_id, kind, candidates=exact
        )

    candidates = tuple(
        sorted(
            (
                _with_score(
                    candidate, float(fuzz.WRatio(normalized_query, _normalize(candidate.name)))
                )
                for candidate in rows
            ),
            key=lambda candidate: (-(candidate.score or 0.0), candidate.name, candidate.value),
        )[:MAX_CANDIDATES]
    )
    if not candidates:
        return LookupResult(LookupStatus.NEEDS_CLARIFICATION, query, class_id, kind)
    best = candidates[0]
    runner_up_score = candidates[1].score if len(candidates) > 1 else None
    if (
        best.score is not None
        and best.score >= FUZZY_ACCEPT_SCORE
        and (runner_up_score is None or best.score - runner_up_score >= FUZZY_ACCEPT_MARGIN)
    ):
        return LookupResult(LookupStatus.MATCH, query, class_id, kind, match=best)
    useful = tuple(
        candidate
        for candidate in candidates
        if candidate.score is not None and candidate.score >= FUZZY_CANDIDATE_SCORE
    )
    return LookupResult(
        LookupStatus.NEEDS_CLARIFICATION,
        query,
        class_id,
        kind,
        candidates=useful or candidates,
    )


class StatsguruIdResolver:
    def __init__(self, fetcher: Fetcher, *, budget: float = 60.0) -> None:
        self._fetcher = fetcher
        self._budget = budget
        self._form_tables: dict[tuple[int, str], dict[int, str]] = {}
        self._involve_tables: dict[tuple[int, str, str], dict[int, str]] = {}

    async def lookup_ground(self, class_id: int, name: str) -> LookupResult:
        return await self._lookup_form_field(class_id, "ground", name)

    async def lookup_series(self, class_id: int, name: str) -> LookupResult:
        return await self._lookup_form_field(class_id, "series", name)

    async def lookup_player_involve(self, class_id: int, name: str) -> LookupResult:
        return await self._lookup_involve(class_id, "player_involve", "search_player", name)

    async def lookup_captain_involve(self, class_id: int, name: str) -> LookupResult:
        return await self._lookup_involve(class_id, "captain_involve", "search_captain", name)

    async def _lookup_form_field(self, class_id: int, field: str, name: str) -> LookupResult:
        table = await self._form_table(class_id, field, force_refetch=False)
        result = resolve_name(class_id, field, name, table)
        if result.status == LookupStatus.MATCH:
            return result
        table = await self._form_table(class_id, field, force_refetch=True)
        return resolve_name(class_id, field, name, table)

    async def _lookup_involve(
        self, class_id: int, field: str, search_field: str, name: str
    ) -> LookupResult:
        table = await self._involve_table(class_id, field, search_field, name, force_refetch=False)
        result = resolve_name(class_id, field, name, table)
        if result.status == LookupStatus.MATCH:
            return result
        table = await self._involve_table(class_id, field, search_field, name, force_refetch=True)
        return resolve_name(class_id, field, name, table)

    async def _form_table(
        self, class_id: int, field: str, *, force_refetch: bool
    ) -> dict[int, str]:
        key = (class_id, field)
        if not force_refetch and key in self._form_tables:
            return self._form_tables[key]
        url = FORM_URL.format(class_id=class_id)
        html = await self._fetcher.fetch(
            url, freshness=Freshness.LOOKUP, budget=self._budget, force_refetch=force_refetch
        )
        table = _options_to_table(parse_filter_form(html).select_lists.get(field, ()), field)
        self._form_tables[key] = table
        return table

    async def _involve_table(
        self,
        class_id: int,
        field: str,
        search_field: str,
        name: str,
        *,
        force_refetch: bool,
    ) -> dict[int, str]:
        normalized_name = _normalize(name)
        key = (class_id, field, normalized_name)
        if not force_refetch and key in self._involve_tables:
            return self._involve_tables[key]
        url = INVOLVE_URL.format(
            class_id=class_id, search_field=search_field, query=quote_plus(name)
        )
        html = await self._fetcher.fetch(
            url, freshness=Freshness.LOOKUP, budget=self._budget, force_refetch=force_refetch
        )
        table = _options_to_table(parse_filter_form(html).checkbox_lists.get(field, ()), field)
        self._involve_tables[key] = table
        return table


def _class_table(
    tables: Mapping[int, Mapping[int, str]], class_id: int, kind: str
) -> Mapping[int, str]:
    try:
        return tables[class_id]
    except KeyError as error:
        raise ValueError(f"unknown class {class_id} for {kind}") from error


def _options_to_table(options: tuple[FormOption, ...], field: str) -> dict[int, str]:
    table: dict[int, str] = {}
    for option in options:
        if not option.value:
            continue
        try:
            value = int(option.value)
        except ValueError as error:
            raise ValueError(f"{field} option {option.label!r} has non-integer value") from error
        table[value] = (
            _clean_involve_label(option.label) if field.endswith("_involve") else option.label
        )
    return table


def _clean_involve_label(label: str) -> str:
    match = _INVOLVE_LABEL_RE.match(label)
    return match.group("name") if match else label


def _with_score(candidate: IdCandidate, score: float) -> IdCandidate:
    return IdCandidate(candidate.class_id, candidate.kind, candidate.value, candidate.name, score)


def _normalize(value: str) -> str:
    value = value.casefold().translate(_PUNCTUATION_TABLE)
    return _SPACE_RE.sub(" ", value).strip()
