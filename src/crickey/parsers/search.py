from __future__ import annotations

import re
from dataclasses import dataclass

from crickey.parsers.common import (
    StatsguruParseError,
    document_from_html,
    element_text,
    first_int_from_path,
    link_params,
)
from crickey.parsers.convert import parse_span

_DETAIL_RE = re.compile(r"\((?P<span>.*?)(?:,\s*(?P<count>\d+)\s+match(?:es)?)?\)")
_FULL_NAME_RE = re.compile(r"^(?P<display>.*?)\s*\((?P<full>[^()]*)\)\s*$")


@dataclass(frozen=True)
class PlayerFormat:
    class_id: int
    label: str
    role: str
    span: object
    match_count: int | None


@dataclass(frozen=True)
class PlayerSearchResult:
    player_id: int
    display_name: str
    full_name: str | None
    country_codes: tuple[str, ...]
    formats: tuple[PlayerFormat, ...]


def parse_player_search(page: str) -> tuple[PlayerSearchResult, ...]:
    doc = document_from_html(page)
    rows = doc.xpath('//tr[.//a[contains(@href, "/ci/engine/player/")]]')
    if not rows:
        raise StatsguruParseError("player search results are missing")
    results: list[PlayerSearchResult] = []
    for row in rows:
        cells = row.xpath("./td")
        if len(cells) < 3:
            continue
        first_link = row.xpath('.//a[contains(@href, "/ci/engine/player/")][1]')[0]
        player_id = first_int_from_path(first_link.get("href"), "/player/")
        if player_id is None:
            raise StatsguruParseError("player search result link is missing the player ID")
        display, full = _parse_name(element_text(cells[0]))
        countries = tuple(
            part.strip() for part in element_text(cells[1]).split("/") if part.strip()
        )
        format_links = cells[2].xpath(
            './/a[contains(@href, "/ci/engine/player/") and contains(@href, "class=")]'
        )
        if not format_links:
            continue
        formats = tuple(_parse_format(link) for link in format_links)
        results.append(PlayerSearchResult(player_id, display, full, countries, formats))
    if not results:
        raise StatsguruParseError("player search result rows with format links are missing")
    return tuple(results)


def _parse_name(value: str) -> tuple[str, str | None]:
    if match := _FULL_NAME_RE.match(value):
        return match.group("display").strip(), match.group("full").strip()
    return value.strip(), None


def _parse_format(link) -> PlayerFormat:
    href = link.get("href", "")
    params = link_params(href)
    try:
        class_id = int(params["class"])
    except (KeyError, ValueError) as error:
        raise StatsguruParseError("player search format link is missing class") from error
    label = element_text(link)
    role = label.rsplit(" ", 1)[-1]
    detail = link.tail or ""
    match = _DETAIL_RE.search(detail)
    span = None
    count = None
    if match:
        span = parse_span(match.group("span"))
        if match.group("count") is not None:
            count = int(match.group("count"))
    return PlayerFormat(class_id=class_id, label=label, role=role, span=span, match_count=count)
