from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from lxml import html
from lxml.html import HtmlElement

from crickey.parsers.convert import clean_text


class StatsguruParseError(ValueError):
    """Raised when Statsguru HTML is missing an expected structure."""


@dataclass(frozen=True)
class LinkInfo:
    text: str
    href: str
    params: dict[str, str]


def document_from_html(page: str) -> HtmlElement:
    if not page.strip():
        raise StatsguruParseError("HTML page is empty")
    return html.fromstring(page)


def element_text(element: HtmlElement) -> str:
    return clean_text(element.text_content())


def link_params(href: str | None) -> dict[str, str]:
    if not href:
        return {}
    query = urlsplit(href).query
    if not query:
        return {}
    return dict(parse_qsl(query.replace(";", "&"), keep_blank_values=True))


def first_int_from_path(href: str | None, marker: str) -> int | None:
    if not href or marker not in href:
        return None
    tail = href.split(marker, 1)[1]
    digits = ""
    for char in tail:
        if char.isdigit():
            digits += char
        elif digits:
            break
    return int(digits) if digits else None


def options_from_select(select: HtmlElement) -> tuple[dict[str, Any], ...]:
    options = []
    for option in select.xpath("./option"):
        options.append(
            {
                "value": option.get("value", ""),
                "label": element_text(option),
                "selected": option.get("selected") is not None,
            }
        )
    return tuple(options)
