from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from crickey.parsers import RecentMatch
from crickey.proof import ProofLink
from crickey.resolve import profile_url


@dataclass(frozen=True)
class RenderPlayer:
    name: str
    player_id: int


@dataclass(frozen=True)
class RenderedTable:
    headers: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]


@dataclass(frozen=True)
class AnswerRenderInput:
    short_answer: str
    table: RenderedTable
    method: tuple[str, ...]
    assumptions: tuple[str, ...]
    proof_links: tuple[ProofLink, ...]
    players: tuple[RenderPlayer, ...]
    as_of: date
    current_or_recent_matches: tuple[RecentMatch, ...] = ()


def render_answer(answer: AnswerRenderInput) -> str:
    lines: list[str] = [answer.short_answer, "", _markdown_table(answer.table), "", "## Method"]
    lines.extend(f"- {item}" for item in answer.method)
    lines.extend(["", "## Assumptions"])
    lines.extend(f"- {item}" for item in (answer.assumptions or ("None.",)))
    lines.extend(["", "## Links"])
    for link in answer.proof_links:
        if link.confirmed:
            lines.append(f"- Answer proof: [{link.label}]({link.url})")
        else:
            formula = f" Formula: {link.formula}." if link.formula else ""
            lines.append(f"- Input/method link: [{link.label}]({link.url}).{formula}")
    for player in answer.players:
        lines.append(f"- [{player.name} profile]({profile_url(player.player_id)})")
    lines.extend(
        [
            "",
            f"As of: {_format_date(answer.as_of)}",
            freshness_line(answer.current_or_recent_matches, today=answer.as_of),
        ]
    )
    return "\n".join(lines)


def freshness_line(matches: tuple[RecentMatch, ...], *, today: date) -> str:
    if not matches:
        return "Freshness: Statsguru did not list any current or recent matches on the proof page."
    newest = max(matches, key=lambda match: match.end_date or match.start_date or date.min)
    newest_date = newest.end_date or newest.start_date
    warning = ""
    if newest.is_live or (newest.end_date is not None and newest.end_date >= today):
        warning = " Warning: a listed match may still be in progress."
    date_text = _format_date(newest_date) if newest_date else "unknown date"
    return (
        f"Freshness: newest match Statsguru included is {newest.name} "
        f"({newest.label}), ending {date_text}.{warning}"
    )


def _markdown_table(table: RenderedTable) -> str:
    rows = [[_cell(value) for value in row] for row in table.rows]
    headers = [_cell(header) for header in table.headers]
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    header_line = (
        "| " + " | ".join(_pad(cell, widths[index]) for index, cell in enumerate(headers)) + " |"
    )
    sep_line = "| " + " | ".join("-" * widths[index] for index in range(len(widths))) + " |"
    body = [
        "| " + " | ".join(_pad(cell, widths[index]) for index, cell in enumerate(row)) + " |"
        for row in rows
    ]
    return "\n".join([header_line, sep_line, *body])


def _cell(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    return str(value)


def _pad(value: str, width: int) -> str:
    return value + " " * (width - len(value))


def _format_date(value: date | None) -> str:
    if value is None:
        return "unknown date"
    months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    return f"{value.day} {months[value.month - 1]} {value.year:04d}"
