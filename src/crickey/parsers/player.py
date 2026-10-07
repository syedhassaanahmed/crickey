from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlsplit

import pandas as pd

from crickey.parsers.common import (
    StatsguruParseError,
    document_from_html,
    element_text,
    first_int_from_path,
)
from crickey.parsers.results import _rows_to_frame, _usable_headers

_BREADCRUMB_PREFIX = "Statistics / Statsguru /"
_DATA1_ROWS_XPATH = './/tr[contains(concat(" ", normalize-space(@class), " "), " data1 ")]'
# Labels Statsguru gives grouped rows, by the filter each grouping sets (R6). Host and continent
# rows share "in ", so only the row's link tells them apart.
_GROUPING_LABEL_PREFIXES = {
    "opposition": "v ",
    "host": "in ",
    "continent": "in ",
    "year": "year ",
    "season": "season ",
}
# Query parameters that shape the page rather than choose its matches.
_PAGE_PARAMS = frozenset(
    {"class", "orderby", "orderbyad", "page", "size", "template", "type", "view"}
)
GROUPING_COLUMNS = ("grouping_key", "grouping_value", "grouping_name")


class PlayerPageNoRecordsError(StatsguruParseError):
    """Raised when a player page says it has no records, as for a format the player never played."""

    def __init__(self, player_name: str | None) -> None:
        super().__init__("Statsguru has no records for this player page")
        self.player_name = player_name


@dataclass(frozen=True)
class PlayerPage:
    career_averages: pd.DataFrame
    innings: pd.DataFrame | None
    profile_id: int | None
    player_name: str | None = None


def parse_player_page(page: str) -> PlayerPage:
    doc = document_from_html(page)
    player_name = _player_name(doc)
    career_tables = doc.xpath(
        '//caption[contains(normalize-space(.), "Career averages")]/ancestor::table[1]'
    )
    if not career_tables:
        if doc.xpath('//td[contains(normalize-space(.), "No records available")]'):
            raise PlayerPageNoRecordsError(player_name)
        raise StatsguruParseError("Career averages table is missing")
    career = _table_to_frame(career_tables[0], default_blank="Grouping")
    profile_id = _profile_id(career_tables[0])

    innings_tables = doc.xpath(
        '//caption[contains(normalize-space(.), "Innings by innings list")]/ancestor::table[1]'
    )
    innings = None
    if innings_tables:
        innings = _table_to_frame(innings_tables[0], default_blank="Scorecard")
    return PlayerPage(
        career_averages=career, innings=innings, profile_id=profile_id, player_name=player_name
    )


def _player_name(doc) -> str | None:
    # The breadcrumb reads "Statistics / Statsguru / <name> / <format>" (R6).
    for link in doc.xpath('//a[contains(@href, "/ci/engine/player/")]'):
        text = element_text(link)
        if text.startswith(_BREADCRUMB_PREFIX):
            name, separator, _format = text.removeprefix(_BREADCRUMB_PREFIX).rpartition(" / ")
            return (name.strip() or None) if separator else None
    return None


def parse_grouped_rows(page: str) -> pd.DataFrame:
    """Read the summary page's "Career summary" table, which splits the page's matches by
    opposition, host country, continent, year and more (R6).

    Each row keeps Statsguru's label in "Grouping" and its figures, plus `grouping_key`, the filter
    that the row's "view innings" link sets (such as "host" or "continent"), `grouping_value`, that
    filter's value, and `grouping_name`, the label without its "v ", "in " or "year " prefix. Rows
    that set several filters at once, such as "won batting first", have no key.
    """
    doc = document_from_html(page)
    tables = doc.xpath(
        '//caption[contains(normalize-space(.), "Career summary")]/ancestor::table[1]'
    )
    if not tables:
        raise StatsguruParseError("Career summary table is missing")
    table = tables[0]
    # Blank one-cell rows separate the groupings.
    rows = [
        row
        for row in table.xpath(_DATA1_ROWS_XPATH)
        if len(row.xpath("./td")) > 1 and "No records available" not in element_text(row)
    ]
    frame = _table_to_frame(table, default_blank="Grouping", rows=rows)
    # The header's sort links carry the page's own query; each row's link repeats it with the
    # row's grouping filter set to the row's value.
    header_links = table.xpath('.//tr[th][1]//a[contains(@href, "/ci/engine/player/")]/@href')
    page_params = _query_params(header_links[0] if header_links else None)
    keys, values, names = zip(*(_row_grouping(row, page_params) for row in rows), strict=True)
    return frame.assign(
        grouping_key=pd.Series(keys, index=frame.index, dtype=object),
        grouping_value=pd.Series(values, index=frame.index, dtype=object),
        grouping_name=pd.Series(names, index=frame.index, dtype=object),
    )


def _row_grouping(
    row, page_params: Mapping[str, tuple[str, ...]]
) -> tuple[str | None, str | None, str]:
    label = element_text(row.xpath("./td")[0])
    row_params = next(
        (
            params
            for params in map(
                _query_params, row.xpath('.//a[contains(@href, "/ci/engine/player/")]/@href')
            )
            if params.get("view") == ("innings",)
        ),
        None,
    )
    if row_params is None or (key := _grouping_key(label, row_params, page_params)) is None:
        return None, None, label
    return key, row_params[key][0], label.removeprefix(_GROUPING_LABEL_PREFIXES.get(key, ""))


def _grouping_key(
    label: str,
    row_params: Mapping[str, tuple[str, ...]],
    page_params: Mapping[str, tuple[str, ...]],
) -> str | None:
    keys = (row_params.keys() | page_params.keys()) - _PAGE_PARAMS
    changed = [key for key in keys if row_params.get(key, ()) != page_params.get(key, ())]
    if not changed:
        # The row's value is also the page's only value for its filter ("in U.A.E." on a page
        # filtered to host 27), so its link is the page's own query and the label tells which.
        changed = [
            key for key in keys if len(row_params.get(key, ())) == 1 and _label_fits(key, label)
        ]
    if len(changed) != 1 or len(row_params.get(changed[0], ())) != 1:
        return None
    return changed[0]


def _label_fits(key: str, label: str) -> bool:
    prefix = _GROUPING_LABEL_PREFIXES.get(key)
    return prefix is not None and label.startswith(prefix)


def _query_params(href: str | None) -> dict[str, tuple[str, ...]]:
    values: dict[str, list[str]] = {}
    query = urlsplit(href or "").query.replace(";", "&")
    for key, value in parse_qsl(query, keep_blank_values=True):
        values.setdefault(key, []).append(value)
    return {key: tuple(sorted(items)) for key, items in values.items()}


def _table_to_frame(table, default_blank: str, rows: list | None = None) -> pd.DataFrame:
    rows = table.xpath(_DATA1_ROWS_XPATH) if rows is None else rows
    if not rows:
        raise StatsguruParseError(
            f"{element_text(table.xpath('./caption')[0]) or 'player'} table has no data rows"
        )
    raw_headers = [element_text(th) for th in table.xpath(".//tr[th][1]/th")]
    if not raw_headers:
        raise StatsguruParseError(
            f"{element_text(table.xpath('./caption')[0]) or 'player'} table header row is missing"
        )
    headers, keep = _usable_headers(raw_headers, rows)
    headers = [
        default_blank if (not raw_headers[i] and header == "Scorecard") else header
        for header, i in zip(headers, keep, strict=True)
    ]
    return _rows_to_frame(headers, keep, rows)


def _profile_id(table) -> int | None:
    link = table.xpath('.//a[contains(@href, "/ci/content/player/")][1]')
    if not link:
        return None
    return first_int_from_path(link[0].get("href"), "/player/")
