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
from crickey.parsers.convert import (
    convert_cell,
    exact_batting_average,
    exact_strike_rate,
    parse_date,
)

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
    date: date | None
    match_id: int
    label: str
    is_live: bool


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
        _add_exact_batting_columns(data)
    return ResultsPage(
        table=data,
        headers=tuple(data.columns),
        totals=_parse_totals(doc),
        current_or_recent_matches=parse_current_or_recent_matches(page),
        no_records=no_records,
    )


def parse_current_or_recent_matches(page: str) -> tuple[RecentMatch, ...]:
    doc = document_from_html(page)
    tables = doc.xpath('//b[contains(normalize-space(.), "current or recent")]/ancestor::table[1]')
    if not tables:
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
        name, parsed_date = _split_recent_name_date(before)
        matches.append(
            RecentMatch(
                name=name, date=parsed_date, match_id=match_id, label=label, is_live="Live" in label
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


def _parse_totals(doc: HtmlElement) -> PageTotals:
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
    return PageTotals(page, pages, showing_from, showing_to, total)


def _split_recent_name_date(value: str) -> tuple[str, date | None]:
    parts = [part.strip() for part in value.rsplit(",", 2)]
    if len(parts) >= 3:
        date_text = f"{parts[-2]}, {parts[-1]}"
        try:
            return ",".join(parts[:-2]).strip(), parse_date(date_text)
        except ValueError:
            return value, None
    return value, None


def _add_exact_batting_columns(data: pd.DataFrame) -> None:
    if {"Runs", "Inns", "NO"}.issubset(data.columns):
        data["exact_batting_average"] = [
            exact_batting_average(row["Runs"], row["Inns"], row["NO"]) for _, row in data.iterrows()
        ]
    if {"Runs", "BF"}.issubset(data.columns):
        data["exact_batting_strike_rate"] = [
            exact_strike_rate(row["Runs"], row["BF"]) for _, row in data.iterrows()
        ]
