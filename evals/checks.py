"""The eval's checks (#18), on a plain transcript that scorers.py builds from each sample."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse

from crickey.ids import LookupResult, lookup_continent, lookup_host, lookup_team, lookup_trophy
from crickey.query import ResolvedPeriod, SymbolicPeriod, SymbolicPeriodKind
from crickey.server import _answer_metric, _format_class, _parse_period, _split_by
from evals.cases import FILTERS, Case, ExpectedCall, ExpectedPeriod, ExpectedPlayer

STATSGURU_HOSTS = frozenset({"stats.cricinfo.com", "stats.espncricinfo.com"})
COST_KEYS = ("tool_calls", "tool_errors", "statsguru_requests", "cached_pages", "tokens", "seconds")

_URL = re.compile(r"https?://[^\s<>()\[\]\"'`|【】]+")
_NUMBER = re.compile(r"(?<![\w.])\d+(?:,\d{3})*(?:\.\d+)?(?!\w)")
_LIST_MARKER = re.compile(r"^(\s*)\d+[.)](?=\s)", re.MULTILINE)
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
# Digit groups split by a space, such as "2 779" or "2\u202f779", as some models write them.
_DIGIT_GROUPS = re.compile(r"(?<![\w.])\d{1,3}(?:[ \u00a0\u202f\u2009]\d{3})+(?:\.\d+)?(?!\w)")
_GROUP_SPACE = re.compile(r"[ \u00a0\u202f\u2009]")
_RULE_CELL = re.compile(r":?-+:?")
_COUNTRY_COUNT = re.compile(r"(\d+) (?:host )?(?:countries|nations)")
_LOOKUPS: dict[str, Callable[[int, str], LookupResult]] = {
    "team": lookup_team,
    "opposition": lookup_team,
    "host_country": lookup_host,
    "continent": lookup_continent,
    "trophy": lookup_trophy,
}
# Arguments that change an answer tool's result, so a call matches only when they agree.
_OTHER_FILTERS = ("home_or_away", "match_result")


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: Mapping[str, Any]
    # What the model read back: crickey's text, or the error message.
    result: str = ""
    error: str | None = None


@dataclass(frozen=True)
class Transcript:
    question: str
    calls: tuple[ToolCall, ...]
    answer: str
    tokens: int = 0
    seconds: float = 0.0
    # From each result's `_meta` (D38).
    statsguru_requests: int = 0
    cached_pages: int = 0


@dataclass(frozen=True)
class CheckResult:
    passed: bool
    explanation: str


def check_answer(case: Case, transcript: Transcript) -> CheckResult:
    """The final answer contains the known answer's facts, outside its links."""
    text = _plain(_URL.sub(" ", transcript.answer))
    missing = [facts for facts in case.facts if not any(_contains(text, fact) for fact in facts)]
    if missing:
        listed = "; ".join(" or ".join(repr(fact) for fact in facts) for facts in missing)
        return CheckResult(False, f"The answer is missing {listed}.")
    return CheckResult(True, f"The answer has all {len(case.facts)} facts.")


def check_tool(case: Case, transcript: Transcript) -> CheckResult:
    """The answer tool the question needs was called and answered."""
    if any(call.name == case.tool and _answered(call) for call in transcript.calls):
        return CheckResult(True, f"{case.tool} answered.")
    used = ", ".join(_call_label(call) for call in transcript.calls) or "no tools"
    return CheckResult(False, f"Expected a {case.tool} call that answers; got {used}.")


def check_arguments(case: Case, transcript: Transcript) -> CheckResult:
    """Each call the question needs was made with the arguments that decide its answer."""
    missing = [
        expected
        for expected in case.calls
        if not any(_matches(expected, call) for call in transcript.calls)
    ]
    if missing:
        made = "; ".join(
            f"{call.name} {json.dumps(call.arguments, default=str)}" for call in transcript.calls
        )
        wanted = "; ".join(_describe(expected) for expected in missing)
        return CheckResult(False, f"No call matched: {wanted}. Calls made: {made or 'none'}.")
    return CheckResult(True, f"All {len(case.calls)} expected calls were made.")


def check_proof(case: Case, transcript: Transcript) -> CheckResult:
    """The answer cites a Statsguru query link, and every link it cites came from a tool (D16)."""
    cited = _urls(transcript.answer)
    returned = set().union(*(_urls(call.result) for call in transcript.calls))
    proof = [url for url in cited if _is_statsguru_query(url)]
    unsourced = [url for url in cited if url not in returned]
    if not proof:
        return CheckResult(False, "The answer cites no Statsguru query link.")
    if unsourced:
        return CheckResult(False, f"The answer cites links no tool returned: {unsourced}.")
    return CheckResult(True, f"The answer cites {len(proof)} Statsguru query link(s) from tools.")


def check_grounding(case: Case, transcript: Transcript) -> CheckResult:
    """Every figure in the answer appears in a tool result or the question (D13)."""
    sources = _numbers(transcript.question).union(
        *(_numbers(call.result) for call in transcript.calls)
    )
    answer = _LIST_MARKER.sub(r"\1", _URL.sub(" ", transcript.answer))
    answer = _without_rank_columns(answer)
    answer = _DIGIT_GROUPS.sub(lambda match: _read_group(match.group(), sources), answer)
    ungrounded = sorted(_numbers(answer) - sources, key=Decimal)
    if ungrounded:
        return CheckResult(False, f"Figures no tool returned: {', '.join(ungrounded)}.")
    return CheckResult(True, "Every figure in the answer came from a tool or the question.")


CHECKS: dict[str, Callable[[Case, Transcript], CheckResult]] = {
    "answer": check_answer,
    "tool": check_tool,
    "arguments": check_arguments,
    "proof": check_proof,
    "grounding": check_grounding,
}


def check_all(case: Case, transcript: Transcript) -> CheckResult:
    """The headline: the question passes every check."""
    failed = [name for name, check in CHECKS.items() if not check(case, transcript).passed]
    if failed:
        return CheckResult(False, f"Failed: {', '.join(failed)}.")
    return CheckResult(True, "Passed every check.")


def cost(transcript: Transcript) -> dict[str, float]:
    """What the question cost: tool calls, tool errors, Statsguru pages, tokens and time."""
    return {
        "tool_calls": len(transcript.calls),
        "tool_errors": sum(call.error is not None for call in transcript.calls),
        "statsguru_requests": transcript.statsguru_requests,
        "cached_pages": transcript.cached_pages,
        "tokens": transcript.tokens,
        "seconds": round(transcript.seconds, 1),
    }


def _matches(expected: ExpectedCall, call: ToolCall) -> bool:
    if call.name != expected.tool:
        return False
    arguments = call.arguments
    class_id = _class_id(arguments.get("format"))
    if expected.format is not None and class_id != _class_id(expected.format):
        return False
    if expected.player is not None and not _same_player(expected.player, call):
        return False
    discipline = str(arguments.get("discipline") or "batting").casefold()
    # The answer tools all default to batting.
    if discipline != (expected.discipline or "batting"):
        return False
    if expected.metrics and not _same_metrics(expected, discipline, arguments):
        return False
    if not _same_period(expected.period, arguments.get("period")):
        return False
    for name in FILTERS:
        if not _same_filter(name, expected.filters.get(name), arguments.get(name), class_id):
            return False
    if any(_given(arguments.get(name)) for name in _OTHER_FILTERS):
        return False
    if _number(arguments.get("minimum")) != _number(expected.minimum):
        return False
    return _same_split(expected.split_by, arguments.get("split_by"))


def _class_id(value: Any) -> int | None:
    try:
        return _format_class(value) if isinstance(value, str | int) else None
    except Exception:
        return None


def _same_player(expected: ExpectedPlayer, call: ToolCall) -> bool:
    arguments = call.arguments
    if _given(arguments.get("player_id")):
        return _number(arguments.get("player_id")) == expected.id
    name = arguments.get("player_name")
    if not isinstance(name, str) or not any(_has_words(name, words) for words in expected.names):
        return False
    # A name counts once crickey resolved it to this player, whose page its answer links;
    # a clarification lists candidates without links (R7).
    return f"/player/{expected.id}.html" in call.result


def _answered(call: ToolCall) -> bool:
    """The call answered: its result has a Statsguru query link, which a clarification lacks."""
    return call.error is None and any(_is_statsguru_query(url) for url in _urls(call.result))


def _is_statsguru_query(url: str) -> bool:
    """A Statsguru query or player page: proof, unlike the profile links answers add (D16)."""
    parsed = urlparse(url)
    return parsed.hostname in STATSGURU_HOSTS and parsed.path.startswith("/ci/engine/")


def _same_metrics(expected: ExpectedCall, discipline: str, arguments: Mapping[str, Any]) -> bool:
    given = arguments.get("metrics", arguments.get("metric"))
    values = given if isinstance(given, list) else [given]
    actual = [_metric_key(discipline, value) for value in values]
    if None in actual or len(actual) != len(expected.metrics):
        return False
    if len(actual) > 1 and str(arguments.get("match", "all")).casefold() != "all":
        return False
    slots = [{_metric_key(discipline, name) for name in choices} for choices in expected.metrics]
    return _assign(actual, slots)


def _assign(actual: list[str | None], slots: list[set[str | None]]) -> bool:
    if not actual:
        return True
    first, rest = actual[0], actual[1:]
    return any(
        first in slot and _assign(rest, slots[:index] + slots[index + 1 :])
        for index, slot in enumerate(slots)
    )


def _metric_key(discipline: str, value: Any) -> str | None:
    try:
        return _answer_metric(discipline, str(value)).key
    except Exception:
        return None


def _same_period(expected: ExpectedPeriod | None, actual: Any) -> bool:
    """Compares the call's period as crickey reads its text (D42)."""
    text = actual.strip() if isinstance(actual, str) else actual
    if not _given(text):
        # Without a period, the answer tools use the whole record or the player's career span.
        return expected is None or expected.kind == "career"
    if not isinstance(text, str):
        return False
    try:
        period = _parse_period(text)
    except ValueError:
        return False
    if expected is None:
        # A case without a period asks about the whole record, which these all give.
        return period is None or period == SymbolicPeriod(kind=SymbolicPeriodKind.CAREER)
    if expected.kind == "all_time":
        return period is None
    if expected.kind == "dates":
        return period == ResolvedPeriod(start=expected.start, end=expected.end)
    if expected.kind == "career" and isinstance(period, ResolvedPeriod):
        # The span crickey resolves for the career makes the same query (R10).
        return (period.start, period.end) == (expected.start, expected.end)
    years = None if expected.kind == "career" else expected.years
    return period == SymbolicPeriod(kind=SymbolicPeriodKind(expected.kind), years=years)


def _same_filter(name: str, expected: str | None, actual: Any, class_id: int | None) -> bool:
    if expected is None:
        return not _given(actual)
    if not _given(actual):
        return False
    return _filter_values(name, expected, class_id) == _filter_values(name, actual, class_id)


def _filter_values(name: str, value: Any, class_id: int | None) -> frozenset[Any] | None:
    values = value if isinstance(value, list) else [value]
    resolved: set[Any] = set()
    for item in values:
        number = _number(item)
        lookup = _LOOKUPS.get(name)
        if number is not None:
            resolved.add(int(number))
        elif lookup is None or class_id is None:
            resolved.add(_plain(str(item)))
        else:
            result = lookup(class_id, str(item))
            if result.match is None:
                return None
            resolved.add(result.match.value)
    return frozenset(resolved)


def _same_split(expected: str | None, actual: Any) -> bool:
    if expected is None:
        return not _given(actual)
    try:
        return _split_by(actual) == _split_by(expected)
    except Exception:
        return False


def _given(value: Any) -> bool:
    return value not in (None, "", [], {})


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def _describe(expected: ExpectedCall) -> str:
    fields = expected.model_dump(exclude_none=True, exclude_defaults=True, mode="json")
    return f"{fields.pop('tool')} {json.dumps(fields)}"


def _call_label(call: ToolCall) -> str:
    return call.name if call.error is None else f"{call.name} (error)"


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).replace("*", "").replace("`", "").replace(":", " ")
    return " ".join(_THOUSANDS.sub("", text).casefold().split())


def _contains(text: str, fact: str) -> bool:
    needle = _plain(fact)
    if _NUMBER.fullmatch(needle):
        return _canonical(needle) in _numbers(text)
    if re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", text) is not None:
        return True
    # A count of countries also reads as the count before a list of that many, "11 (a, …)".
    count = _COUNTRY_COUNT.fullmatch(needle)
    return count is not None and _lists_count(text, int(count.group(1)))


def _lists_count(text: str, count: int) -> bool:
    for match in re.finditer(rf"(?<![\w.]){count} \(([^()]*)\)", text):
        items = [item for item in re.split(r",|\band\b", match.group(1)) if item.strip()]
        if len(items) == count:
            return True
    return False


def _without_rank_columns(text: str) -> str:
    """Blanks the numbers of table columns that count rows 1, 2, 3, …, which aren't figures."""
    lines = text.split("\n")
    start = 0
    while start < len(lines):
        end = start
        while end < len(lines) and lines[end].lstrip().startswith("|"):
            end += 1
        if end == start:
            start += 1
            continue
        rows = [_cells(line) for line in lines[start:end]]
        if len(rows) > 2 and all(_RULE_CELL.fullmatch(cell) for cell in rows[1]):
            body = rows[2:]
            for column in range(max(len(row) for row in body)):
                # Other cells in the column, such as "—" for an unranked row, don't count.
                cells = [row[column] for row in body if column < len(row)]
                ranks = [rank for rank in map(_rank, cells) if rank is not None]
                if len(ranks) >= 3 and ranks == list(range(1, len(ranks) + 1)):
                    for row in body:
                        if column < len(row) and _rank(row[column]) is not None:
                            row[column] = ""
            lines[start + 2 : end] = ["| " + " | ".join(row) + " |" for row in body]
        start = end
    return "\n".join(lines)


def _rank(cell: str) -> int | None:
    value = cell.strip("* ")
    return int(value) if value.isdigit() else None


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _read_group(group: str, sources: set[str]) -> str:
    """A digit group reads as one number when a tool returned it, and as separate ones otherwise."""
    joined = _GROUP_SPACE.sub("", group)
    return joined if _canonical(joined) in sources else group


def _has_words(name: str, words: str) -> bool:
    return _contains(_plain(name.replace(".", " ")), words)


def _urls(text: str) -> set[str]:
    return {url.rstrip(".,;:!?*_") for url in _URL.findall(text)}


def _numbers(text: str) -> set[str]:
    return {_canonical(match) for match in _NUMBER.findall(_URL.sub(" ", text))}


def _canonical(number: str) -> str:
    try:
        value = Decimal(number.replace(",", ""))
    except InvalidOperation:
        return number
    return format(value.normalize(), "f")
