from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from crickey.parsers.common import (
    StatsguruParseError,
    document_from_html,
    element_text,
    first_int_from_path,
)
from crickey.parsers.results import _add_exact_batting_columns, _rows_to_frame, _usable_headers


@dataclass(frozen=True)
class PlayerPage:
    career_averages: pd.DataFrame
    innings: pd.DataFrame | None
    profile_id: int | None


def parse_player_page(page: str) -> PlayerPage:
    doc = document_from_html(page)
    career_tables = doc.xpath(
        '//caption[contains(normalize-space(.), "Career averages")]/ancestor::table[1]'
    )
    if not career_tables:
        raise StatsguruParseError("Career averages table is missing")
    career = _table_to_frame(career_tables[0], default_blank="Grouping")
    _add_exact_batting_columns(career)
    profile_id = _profile_id(career_tables[0])

    innings_tables = doc.xpath(
        '//caption[contains(normalize-space(.), "Innings by innings list")]/ancestor::table[1]'
    )
    innings = None
    if innings_tables:
        innings = _table_to_frame(innings_tables[0], default_blank="Scorecard")
    return PlayerPage(career_averages=career, innings=innings, profile_id=profile_id)


def _table_to_frame(table, default_blank: str) -> pd.DataFrame:
    rows = table.xpath('.//tr[contains(concat(" ", normalize-space(@class), " "), " data1 ")]')
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
