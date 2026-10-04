from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

import pandas as pd
from lxml.html import HtmlElement

from crickey.parsers.common import (
    StatsguruParseError,
    document_from_html,
    element_text,
    first_int_from_path,
)
from crickey.parsers.convert import clean_text, convert_cell, parse_date

_PAGE_RE = re.compile(r"Page\s+(\d+)\s+of\s+(\d+)", re.I)
_SHOWING_RE = re.compile(r"Showing\s+(\d+)\s+-\s+(\d+)\s+of\s+(\d+)", re.I)
_TEAMS_RE = re.compile(r"\(([^()]*)\)\s*$")


@dataclass(frozen=True)
class PlayerCell:
    name: str
    player_id: int | None
    team_codes: tuple[str, ...]


@dataclass(frozen=True)
class PageTotals:
    page: int | None
    pages: int | None
    showing_from: int | None
    showing_to: int | None
    total: int | None


@dataclass(frozen=True)
class RecentMatch:
    name: str
    start_date: date | None
    end_date: date | None
    match_id: int
    label: str
    is_live: bool

    @property
    def date(self) -> date | None:
        return self.start_date


@dataclass(frozen=True)
class ResultsPage:
    table: pd.DataFrame
    headers: tuple[str, ...]
    totals: PageTotals
    current_or_recent_matches: tuple[RecentMatch, ...]
    no_records: bool = False


def parse_results_page(page: str) -> ResultsPage:
    doc = document_from_html(page)
    table = _find_results_table(doc)
    rows = table.xpath('.//tr[contains(concat(" ", normalize-space(@class), " "), " data1 ")]')
    if not rows:
        raise StatsguruParseError("results table has no data rows")

    no_records = any("No records available" in element_text(row) for row in rows)
    raw_headers = [element_text(th) for th in table.xpath(".//tr[th][1]/th")]
    if no_records and not raw_headers:
        data = pd.DataFrame()
        headers: tuple[str, ...] = ()
    else:
        if not raw_headers:
            raise StatsguruParseError("results table header row is missing")
        headers, keep_indexes = _usable_headers(raw_headers, rows)
        data = _rows_to_frame(headers, keep_indexes, rows)
    return ResultsPage(
        table=data,
        headers=tuple(data.columns),
        totals=_parse_totals(doc, require=not no_records),
        current_or_recent_matches=parse_current_or_recent_matches(page),
        no_records=no_records,
    )


def parse_current_or_recent_matches(page: str) -> tuple[RecentMatch, ...]:
    doc = document_from_html(page)
    tables = doc.xpath('//b[contains(normalize-space(.), "current or recent")]/ancestor::table[1]')
    if not tables:
        data2_match_tables = doc.xpath(
            '//table[.//tr[contains(concat(" ", normalize-space(@class), " "), " data2 ")]'
            ' and .//a[contains(@href, "/ci/engine/match/")]]'
        )
        if data2_match_tables:
            raise StatsguruParseError("current or recent matches heading is missing")
        return ()
    matches: list[RecentMatch] = []
    for row in tables[0].xpath(
        './tr[contains(concat(" ", normalize-space(@class), " "), " data2 ")]'
    )[1:]:
        link = row.xpath('.//a[contains(@href, "/ci/engine/match/")][1]')
        if not link:
            continue
        href = link[0].get("href", "")
        match_id = first_int_from_path(href, "/match/")
        if match_id is None:
            continue
        label = element_text(link[0])
        text = element_text(row)
        before = text.split("[", 1)[0].strip()
        name, start_date, end_date = _split_recent_name_dates(before)
        matches.append(
            RecentMatch(
                name=name,
                start_date=start_date,
                end_date=end_date,
                match_id=match_id,
                label=label,
                is_live="Live" in label,
            )
        )
    return tuple(matches)


def _find_results_table(doc: HtmlElement) -> HtmlElement:
    tables = doc.xpath(
        'self::table[contains(concat(" ", normalize-space(@class), " "), " engineTable ")]'
        ' | //table[contains(concat(" ", normalize-space(@class), " "), " engineTable ")]'
    )
    data_tables = [
        table
        for table in tables
        if table.xpath('.//tr[contains(concat(" ", normalize-space(@class), " "), " data1 ")]')
    ]
    saw_caption = False
    for table in data_tables:
        caption = element_text(table.xpath("./caption")[0]) if table.xpath("./caption") else ""
        saw_caption = saw_caption or bool(caption)
        if caption and (table.xpath(".//tr[th]") or "No records available" in element_text(table)):
            return table
    if data_tables:
        if saw_caption:
            raise StatsguruParseError("results table header row is missing")
        raise StatsguruParseError("results table caption is missing")
    raise StatsguruParseError("results table is missing")


def _usable_headers(raw_headers: list[str], rows: list[HtmlElement]) -> tuple[list[str], list[int]]:
    max_cells = max(len(row.xpath("./td")) for row in rows)
    headers = list(raw_headers) + [""] * max(0, max_cells - len(raw_headers))
    used: list[str] = []
    keep: list[int] = []
    for index, header in enumerate(headers[:max_cells]):
        column_has_content = any(
            element_text(row.xpath("./td")[index]) for row in rows if len(row.xpath("./td")) > index
        )
        if not header and not column_has_content:
            continue
        if not header:
            header = "Match" if index == max_cells - 1 else "Scorecard"
        original = header
        suffix = 2
        while header in used:
            header = f"{original} {suffix}"
            suffix += 1
        used.append(header)
        keep.append(index)
    return used, keep


def _rows_to_frame(
    headers: list[str], keep_indexes: list[int], rows: list[HtmlElement]
) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    for row in rows:
        if "No records available" in element_text(row):
            continue
        cells = row.xpath("./td")
        if len(cells) < max(keep_indexes, default=-1) + 1:
            raise StatsguruParseError("results table row has fewer cells than the header")
        record: dict[str, object] = {}
        for header, index in zip(headers, keep_indexes, strict=True):
            text = element_text(cells[index])
            record[header] = convert_cell(header, text)
            if _is_not_out_score(text):
                record[f"{header}_not_out"] = True
            elif header in {"Runs", "HS"}:
                record[f"{header}_not_out"] = False
            if header == "Player":
                player = parse_player_cell(cells[index])
                record["player_name"] = player.name
                record["player_id"] = player.player_id
                record["player_team_codes"] = player.team_codes
            if header == "Match":
                link = cells[index].xpath('.//a[contains(@href, "/ci/engine/match/")][1]')
                if link:
                    record["match_id"] = first_int_from_path(link[0].get("href"), "/match/")
        records.append(record)
    frame = pd.DataFrame(records, dtype=object)
    return frame.where(pd.notna(frame), None)


def parse_player_cell(cell: HtmlElement) -> PlayerCell:
    text = element_text(cell)
    teams_match = _TEAMS_RE.search(text)
    teams = (
        tuple(part.strip() for part in teams_match.group(1).split("/") if part.strip())
        if teams_match
        else ()
    )
    link = cell.xpath('.//a[contains(@href, "/ci/content/player/")][1]')
    player_id = first_int_from_path(link[0].get("href"), "/player/") if link else None
    if link:
        name = element_text(link[0])
    else:
        name = _TEAMS_RE.sub("", text).strip()
    return PlayerCell(name=name, player_id=player_id, team_codes=teams)


def _parse_totals(doc: HtmlElement, *, require: bool = False) -> PageTotals:
    page = pages = showing_from = showing_to = total = None
    text = " ".join(
        element_text(td) for td in doc.xpath('//td[contains(., "Page") or contains(., "Showing")]')
    )
    if match := _PAGE_RE.search(text):
        page = int(match.group(1))
        pages = int(match.group(2))
    if match := _SHOWING_RE.search(text):
        showing_from = int(match.group(1))
        showing_to = int(match.group(2))
        total = int(match.group(3))
    if require and (page is None or pages is None or total is None):
        raise StatsguruParseError("results page paging totals are missing")
    return PageTotals(page, pages, showing_from, showing_to, total)


def _split_recent_name_dates(value: str) -> tuple[str, date | None, date | None]:
    patterns = (
        (
            re.compile(
                r"^(?P<name>.+), (?P<smon>[A-Z][a-z]{2}) (?P<sday>\d{1,2}), "
                r"(?P<syear>\d{4})-(?P<emon>[A-Z][a-z]{2}) (?P<eday>\d{1,2}), "
                r"(?P<eyear>\d{4})$"
            ),
            _dates_full_year_range,
        ),
        (
            re.compile(
                r"^(?P<name>.+), (?P<smon>[A-Z][a-z]{2}) (?P<sday>\d{1,2})-"
                r"(?P<emon>[A-Z][a-z]{2}) (?P<eday>\d{1,2}), (?P<year>\d{4})$"
            ),
            _dates_month_range,
        ),
        (
            re.compile(
                r"^(?P<name>.+), (?P<smon>[A-Z][a-z]{2}) (?P<sday>\d{1,2})-"
                r"(?P<eday>\d{1,2}), (?P<year>\d{4})$"
            ),
            _dates_same_month_range,
        ),
        (
            re.compile(r"^(?P<name>.+), (?P<mon>[A-Z][a-z]{2}) (?P<day>\d{1,2}), (?P<year>\d{4})$"),
            _dates_single,
        ),
    )
    for pattern, parser in patterns:
        if match := pattern.match(value):
            start, end = parser(match)
            return clean_text(match.group("name")), start, end
    raise StatsguruParseError(f"current or recent match date is not recognised: {value!r}")


def _month_number(month: str) -> int:
    return parse_date(f"1 {month} 2000").month


def _make_date(day: str, month: str, year: str | int) -> date:
    return parse_date(f"{int(day)} {month} {int(year)}")


def _dates_full_year_range(match: re.Match[str]) -> tuple[date, date]:
    return (
        _make_date(match.group("sday"), match.group("smon"), match.group("syear")),
        _make_date(match.group("eday"), match.group("emon"), match.group("eyear")),
    )


def _dates_month_range(match: re.Match[str]) -> tuple[date, date]:
    end_year = int(match.group("year"))
    start_year = end_year
    if _month_number(match.group("smon")) > _month_number(match.group("emon")):
        start_year -= 1
    return (
        _make_date(match.group("sday"), match.group("smon"), start_year),
        _make_date(match.group("eday"), match.group("emon"), end_year),
    )


def _dates_same_month_range(match: re.Match[str]) -> tuple[date, date]:
    return (
        _make_date(match.group("sday"), match.group("smon"), match.group("year")),
        _make_date(match.group("eday"), match.group("smon"), match.group("year")),
    )


def _dates_single(match: re.Match[str]) -> tuple[date, date]:
    parsed = _make_date(match.group("day"), match.group("mon"), match.group("year"))
    return parsed, parsed


def _is_not_out_score(value: str) -> bool:
    text = element_text(value) if isinstance(value, HtmlElement) else clean_text(value)
    return text.endswith("*") and text[:-1].replace(",", "").isdigit()
