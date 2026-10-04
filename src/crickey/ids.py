from __future__ import annotations

import re
import string
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from urllib.parse import quote_plus

from rapidfuzz import fuzz
from rapidfuzz.distance import OSA

from crickey import id_tables
from crickey.fetcher import Fetcher, Freshness
from crickey.parsers import FormOption, parse_filter_form

FORM_URL = "https://stats.cricinfo.com/ci/engine/stats/index.html?class={class_id};filter=advanced;type=batting"
INVOLVE_URL = (
    "https://stats.cricinfo.com/ci/engine/stats/index.html?"
    "class={class_id};filter=advanced;{search_field}={query};type=batting"
)

# Fuzzy matching policy: deterministic tiers win first. Otherwise fuzzy matches are accepted
# only for unique whole-word containment or when fuzz.ratio is at least three points ahead of
# the next candidate. WRatio still ranks clarification candidates.
FUZZY_ACCEPT_SCORE = 90.0
FUZZY_ACCEPT_MARGIN = 3.0
FUZZY_CANDIDATE_SCORE = 75.0
MAX_CANDIDATES = 5

_PUNCTUATION_TABLE = str.maketrans({char: " " for char in string.punctuation})
_SPACE_RE = re.compile(r"\s+")
_INVOLVE_LABEL_RE = re.compile(r'^found using "[^"]+":\s*(?P<name>.+)$')
_ACRONYM_SKIP_WORDS = frozenset({"and", "of", "the"})
_GENERIC_WORDS = frozenset({"cricket", "icc", "mens", "the"})
_FORMAT_WORDS_BY_CLASS = {
    1: frozenset({"test"}),
    2: frozenset({"day", "international", "odi", "one"}),
    3: frozenset({"international", "t20", "t20i"}),
    6: frozenset({"t20"}),
    11: frozenset({"international"}),
}


class FetchCall(Protocol):
    async def fetch(
        self,
        url: str,
        *,
        freshness: Freshness,
        force_refetch: bool = False,
    ) -> str: ...


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
        return LookupResult(LookupStatus.NEEDS_CLARIFICATION, query, class_id, kind)
    normalized_query = _normalize(query)
    rows = tuple(IdCandidate(class_id, kind, value, label) for value, label in table.items())
    exact = tuple(
        candidate
        for candidate in rows
        if normalized_query in _normalized_name_variants(candidate.name, kind)
    )
    if len(exact) == 1:
        return LookupResult(LookupStatus.MATCH, query, class_id, kind, match=exact[0])
    if len(exact) > 1:
        return LookupResult(
            LookupStatus.NEEDS_CLARIFICATION, query, class_id, kind, candidates=exact
        )

    acronym = tuple(
        candidate for candidate in rows if _initials(candidate.name, kind) == normalized_query
    )
    if len(acronym) == 1:
        return LookupResult(LookupStatus.MATCH, query, class_id, kind, match=acronym[0])
    if len(acronym) > 1:
        return LookupResult(
            LookupStatus.NEEDS_CLARIFICATION, query, class_id, kind, candidates=acronym
        )

    generic = tuple(
        candidate
        for candidate in rows
        if _generic_words_match(candidate.name, query, class_id, kind)
    )
    if len(generic) == 1:
        return LookupResult(LookupStatus.MATCH, query, class_id, kind, match=generic[0])
    if len(generic) > 1:
        return LookupResult(
            LookupStatus.NEEDS_CLARIFICATION, query, class_id, kind, candidates=generic
        )

    prefix = _single_word_prefix_matches(normalized_query, rows)
    if len(prefix) == 1:
        return LookupResult(LookupStatus.MATCH, query, class_id, kind, match=prefix[0])
    if len(prefix) > 1:
        return LookupResult(
            LookupStatus.NEEDS_CLARIFICATION,
            query,
            class_id,
            kind,
            candidates=_rank_by_wratio(normalized_query, prefix),
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
    contained = _query_containment_matches(normalized_query, rows)
    if len(contained) == 1 and _candidate_contains_query_in_order(normalized_query, contained[0]):
        return LookupResult(LookupStatus.MATCH, query, class_id, kind, match=contained[0])
    if contained:
        return LookupResult(
            LookupStatus.NEEDS_CLARIFICATION,
            query,
            class_id,
            kind,
            candidates=_rank_by_wratio(normalized_query, contained),
        )
    scored_by_ratio = tuple(
        sorted(
            (
                _with_score(
                    candidate, float(fuzz.ratio(normalized_query, _normalize(candidate.name)))
                )
                for candidate in rows
            ),
            key=lambda candidate: (-(candidate.score or 0.0), candidate.name, candidate.value),
        )
    )
    ratio_best = scored_by_ratio[0]
    ratio_runner_up_score = scored_by_ratio[1].score if len(scored_by_ratio) > 1 else None
    if (
        ratio_best.score is not None
        and ratio_best.score >= FUZZY_ACCEPT_SCORE
        and _query_words_fit_candidate_typos(normalized_query, ratio_best)
        and (
            ratio_runner_up_score is None
            or ratio_best.score - ratio_runner_up_score >= FUZZY_ACCEPT_MARGIN
        )
    ):
        return LookupResult(LookupStatus.MATCH, query, class_id, kind, match=ratio_best)
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

    async def lookup_ground(
        self, class_id: int, name: str, *, call: FetchCall | None = None
    ) -> LookupResult:
        return await self._with_call(
            call, lambda fetch_call: self._lookup_form_field(class_id, "ground", name, fetch_call)
        )

    async def lookup_series(
        self, class_id: int, name: str, *, call: FetchCall | None = None
    ) -> LookupResult:
        return await self._with_call(
            call, lambda fetch_call: self._lookup_form_field(class_id, "series", name, fetch_call)
        )

    async def lookup_player_involve(
        self, class_id: int, name: str, *, call: FetchCall | None = None
    ) -> LookupResult:
        return await self._with_call(
            call,
            lambda fetch_call: self._lookup_involve(
                class_id, "player_involve", "search_player", name, fetch_call
            ),
        )

    async def lookup_captain_involve(
        self, class_id: int, name: str, *, call: FetchCall | None = None
    ) -> LookupResult:
        return await self._with_call(
            call,
            lambda fetch_call: self._lookup_involve(
                class_id, "captain_involve", "search_captain", name, fetch_call
            ),
        )

    async def _with_call(
        self,
        call: FetchCall | None,
        lookup: Callable[[FetchCall], Awaitable[LookupResult]],
    ) -> LookupResult:
        if call is not None:
            return await lookup(call)
        async with self._fetcher.call(budget=self._budget) as fetch_call:
            return await lookup(fetch_call)

    async def _lookup_form_field(
        self, class_id: int, field: str, name: str, call: FetchCall
    ) -> LookupResult:
        table, can_refetch = await self._form_table(class_id, field, call, force_refetch=False)
        result = resolve_name(class_id, field, name, table)
        if (
            result.status == LookupStatus.MATCH
            or not can_refetch
            or _ambiguous_result_has_plausible_cached_candidates(result, table)
        ):
            return result
        table, _ = await self._form_table(class_id, field, call, force_refetch=True)
        return resolve_name(class_id, field, name, table)

    async def _lookup_involve(
        self, class_id: int, field: str, search_field: str, name: str, call: FetchCall
    ) -> LookupResult:
        table, can_refetch = await self._involve_table(
            class_id, field, search_field, name, call, force_refetch=False
        )
        result = resolve_name(class_id, field, name, table)
        if (
            result.status == LookupStatus.MATCH
            or not can_refetch
            or _ambiguous_result_has_plausible_cached_candidates(result, table)
        ):
            return result
        table, _ = await self._involve_table(
            class_id, field, search_field, name, call, force_refetch=True
        )
        return resolve_name(class_id, field, name, table)

    async def _form_table(
        self, class_id: int, field: str, call: FetchCall, *, force_refetch: bool
    ) -> tuple[dict[int, str], bool]:
        key = (class_id, field)
        if not force_refetch and key in self._form_tables:
            return self._form_tables[key], True
        url = FORM_URL.format(class_id=class_id)
        page_was_cached = (
            not force_refetch
            and self._fetcher.cache.get(url, self._fetcher.clock.monotonic()) is not None
        )
        html = await call.fetch(url, freshness=Freshness.LOOKUP, force_refetch=force_refetch)
        table = _options_to_table(parse_filter_form(html).select_lists.get(field, ()), field)
        self._form_tables[key] = table
        return table, page_was_cached

    async def _involve_table(
        self,
        class_id: int,
        field: str,
        search_field: str,
        name: str,
        call: FetchCall,
        *,
        force_refetch: bool,
    ) -> tuple[dict[int, str], bool]:
        normalized_name = _normalize(name)
        key = (class_id, field, normalized_name)
        if not force_refetch and key in self._involve_tables:
            return self._involve_tables[key], True
        url = INVOLVE_URL.format(
            class_id=class_id, search_field=search_field, query=quote_plus(name)
        )
        html = await call.fetch(url, freshness=Freshness.LOOKUP, force_refetch=force_refetch)
        table = _options_to_table(parse_filter_form(html).checkbox_lists.get(field, ()), field)
        self._involve_tables[key] = table
        return table, False


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


def _normalized_name_variants(value: str, kind: str) -> frozenset[str]:
    variants = {value}
    if ":" in value:
        variants.add(value.split(":", 1)[1].strip())
    if kind.endswith("_involve"):
        variants |= {_strip_trailing_parenthetical(variant) for variant in tuple(variants)}
    variants |= {_strip_trailing_year(variant) for variant in tuple(variants)}
    return frozenset(_normalize(variant) for variant in variants)


def _strip_trailing_parenthetical(value: str) -> str:
    return re.sub(r"\s+\([A-Z]{2,3}\)\s*$", "", value).strip()


def _strip_trailing_year(value: str) -> str:
    return re.sub(r",?\s+\d{4}(?:/\d{2,4})?\s*$", "", value).strip()


def _initials(value: str, kind: str) -> str:
    if kind == "ground":
        value = _without_label_prefix(value)
    return "".join(word[0] for word in _normalize(value).split() if word not in _ACRONYM_SKIP_WORDS)


def _without_generic_words(value: str, class_id: int) -> str:
    without_apostrophes = value.replace("'", "").replace("’", "")
    dropped_words = _GENERIC_WORDS | _FORMAT_WORDS_BY_CLASS.get(class_id, frozenset())
    words = [
        singular
        for word in _normalize(without_apostrophes).split()
        if word not in _GENERIC_WORDS
        for singular in (_singularize(word),)
        if singular not in dropped_words
    ]
    return " ".join(words)


def _generic_words_match(candidate_name: str, query: str, class_id: int, kind: str) -> bool:
    if kind == "series" and _digit_words(candidate_name) != _digit_words(query):
        return False
    return _without_generic_words(candidate_name, class_id) == _without_generic_words(
        query, class_id
    )


def _digit_words(value: str) -> frozenset[str]:
    return frozenset(word for word in _normalize(value).split() if _has_digit(word))


def _query_containment_matches(
    normalized_query: str, rows: tuple[IdCandidate, ...]
) -> tuple[IdCandidate, ...]:
    query_words = normalized_query.split()
    if not query_words:
        return ()
    return tuple(
        candidate
        for candidate in rows
        if all(word in _normalize(candidate.name).split() for word in query_words)
    )


def _single_word_prefix_matches(
    normalized_query: str, rows: tuple[IdCandidate, ...]
) -> tuple[IdCandidate, ...]:
    if len(normalized_query) < 3 or " " in normalized_query:
        return ()
    return tuple(
        candidate
        for candidate in rows
        if any(word.startswith(normalized_query) for word in _normalize(candidate.name).split())
    )


def _rank_by_wratio(
    normalized_query: str, rows: tuple[IdCandidate, ...]
) -> tuple[IdCandidate, ...]:
    scored = (
        (
            _with_score(
                candidate, float(fuzz.WRatio(normalized_query, _normalize(candidate.name)))
            ),
            float(fuzz.ratio(normalized_query, _normalize(candidate.name))),
        )
        for candidate in rows
    )
    return tuple(
        candidate
        for candidate, _ratio in sorted(
            scored, key=lambda item: (-(item[0].score or 0.0), -item[1], item[0].value)
        )[:MAX_CANDIDATES]
    )


def _without_label_prefix(value: str) -> str:
    return value.split(":", 1)[1].strip() if ":" in value else value


def _singularize(word: str) -> str:
    if len(word) > 3 and word.endswith("s"):
        return word[:-1]
    return word


def _query_words_fit_candidate_typos(normalized_query: str, candidate: IdCandidate) -> bool:
    query_words = normalized_query.split()
    candidate_words = _normalize(candidate.name).split()
    if _space_insensitive_words_match(query_words, candidate_words):
        return True
    return _consume_matching_words(query_words, candidate_words)


def _space_insensitive_words_match(query_words: list[str], name_words: list[str]) -> bool:
    return bool(query_words) and "".join(query_words) == "".join(name_words)


def _consume_matching_words(query_words: list[str], name_words: list[str]) -> bool:
    if not query_words:
        return True
    query_word = query_words[0]
    for index, name_word in enumerate(name_words):
        remaining_name_words = name_words[:index] + name_words[index + 1 :]
        if query_word == name_word and _consume_matching_words(
            query_words[1:], remaining_name_words
        ):
            return True
        if _has_digit(query_word) or _has_digit(name_word):
            continue
        if len(query_word) < 3 or len(name_word) < 3:
            continue
        max_distance = 2 if len(name_word) >= 8 else 1
        if OSA.distance(query_word, name_word) <= max_distance and _consume_matching_words(
            query_words[1:], remaining_name_words
        ):
            return True
        if len(query_words) >= 2 and query_word != name_word and query_words[1] != name_word:
            split_query_word = query_word + query_words[1]
            if OSA.distance(
                split_query_word, name_word
            ) <= max_distance and _consume_matching_words(query_words[2:], remaining_name_words):
                return True
    return False


def _has_digit(word: str) -> bool:
    return any(char.isdigit() for char in word)


def _candidate_contains_query_in_order(normalized_query: str, candidate: IdCandidate) -> bool:
    query_words = normalized_query.split()
    candidate_words = _normalize(candidate.name).split()
    position = _contiguous_word_position(query_words, candidate_words)
    if position is None:
        return False
    if candidate.kind == "series":
        return position == 0 and _digit_words(normalized_query) == _digit_words(candidate.name)
    return True


def _ambiguous_result_has_plausible_cached_candidates(
    result: LookupResult, table: Mapping[int, str]
) -> bool:
    if result.status == LookupStatus.MATCH or not result.candidates:
        return False
    normalized_query = _normalize(result.query)
    candidates = result.candidates
    return (
        all(
            normalized_query in _normalized_name_variants(candidate.name, result.kind)
            for candidate in candidates
        )
        or all(
            _initials(candidate.name, result.kind) == normalized_query for candidate in candidates
        )
        or all(
            _generic_words_match(candidate.name, result.query, result.class_id, result.kind)
            for candidate in candidates
        )
        or _candidates_contain_query_words(result)
    ) and all(candidate.value in table for candidate in candidates)


def _candidates_contain_query_words(result: LookupResult) -> bool:
    query_words = _normalize(result.query).split()
    return bool(result.candidates) and all(
        _words_appear_in_order(query_words, _normalize(candidate.name).split())
        for candidate in result.candidates
    )


def _words_appear_contiguously(query_words: list[str], name_words: list[str]) -> bool:
    return _contiguous_word_position(query_words, name_words) is not None


def _contiguous_word_position(query_words: list[str], name_words: list[str]) -> int | None:
    if not query_words:
        return 0
    last_start = len(name_words) - len(query_words) + 1
    for start in range(last_start):
        if name_words[start : start + len(query_words)] == query_words:
            return start
    return None


def _words_appear_in_order(query_words: list[str], name_words: list[str]) -> bool:
    position = 0
    for query_word in query_words:
        try:
            position = name_words.index(query_word, position) + 1
        except ValueError:
            return False
    return True
