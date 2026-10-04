from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import Any

_MISSING = {"", "-", "DNB", "TDNB"}
_DATE_FORMATS = ("%d %b %Y", "%b %d, %Y")
_INT_RE = re.compile(r"[+-]?\d[\d,]*")
_DECIMAL_RE = re.compile(r"[+-]?\d+(?:\.\d+)?")


@dataclass(frozen=True)
class Score:
    runs: int
    not_out: bool = False


@dataclass(frozen=True)
class Overs:
    overs: int
    balls: int = 0

    @property
    def total_balls(self) -> int:
        return self.overs * 6 + self.balls


@dataclass(frozen=True)
class Span:
    start: str
    end: str | None = None


def clean_text(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split())


def parse_date(value: str) -> date:
    text = clean_text(value)
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"expected Statsguru date, got {value!r}")


def parse_span(value: str) -> Span | None:
    text = clean_text(value)
    if text in _MISSING:
        return None
    match = re.match(r"^(.+?)\s+-\s+(.+)$", text)
    if match is None:
        match = re.match(r"^([^\s/]+)-(\S.+)$", text)
    if match is None:
        return Span(text)
    return Span(clean_text(match.group(1)), clean_text(match.group(2)))


def parse_score(value: str) -> Score | None:
    text = clean_text(value)
    if text in _MISSING:
        return None
    not_out = text.endswith("*")
    number = text[:-1] if not_out else text
    if not number.replace(",", "").isdigit():
        raise ValueError(f"expected score, got {value!r}")
    return Score(int(number.replace(",", "")), not_out)


def parse_overs(value: str) -> Overs | None:
    text = clean_text(value)
    if text in _MISSING:
        return None
    if "." in text:
        over_text, ball_text = text.split(".", 1)
        if not over_text.isdigit() or not ball_text.isdigit() or len(ball_text) != 1:
            raise ValueError(f"expected overs, got {value!r}")
        balls = int(ball_text)
        if balls > 5:
            raise ValueError(f"expected legal balls in overs, got {value!r}")
        return Overs(int(over_text), balls)
    if not text.isdigit():
        raise ValueError(f"expected overs, got {value!r}")
    return Overs(int(text), 0)


def as_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, Score):
        return value.runs
    if isinstance(value, Overs):
        return value.total_balls
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    if isinstance(value, Decimal):
        return int(value)
    text = clean_text(str(value)).removesuffix("*").replace(",", "")
    if text in _MISSING:
        return None
    if text.isdigit():
        return int(text)
    return None


def convert_cell(header: str, value: str) -> Any:
    text = clean_text(value)
    if text in _MISSING:
        return None
    if header in {"Span"}:
        return parse_span(text)
    if header in {"Start Date", "Date"}:
        return parse_date(text)
    if header == "Overs":
        return parse_overs(text)
    if text.endswith("*") and text[:-1].replace(",", "").isdigit():
        return int(text[:-1].replace(",", ""))
    if _INT_RE.fullmatch(text):
        return int(text.replace(",", ""))
    if _DECIMAL_RE.fullmatch(text):
        try:
            return Decimal(text)
        except InvalidOperation:
            return text
    return text


def exact_batting_average(runs: Any, innings: Any, not_outs: Any) -> Fraction | None:
    run_count = as_int(runs)
    innings_count = as_int(innings)
    not_out_count = as_int(not_outs)
    if run_count is None or innings_count is None or not_out_count is None:
        return None
    outs = innings_count - not_out_count
    if outs <= 0:
        return None
    return Fraction(run_count, outs)


def exact_strike_rate(runs: Any, balls: Any) -> Fraction | None:
    run_count = as_int(runs)
    ball_count = as_int(balls)
    if run_count is None or ball_count in {None, 0}:
        return None
    return Fraction(run_count * 100, ball_count)
