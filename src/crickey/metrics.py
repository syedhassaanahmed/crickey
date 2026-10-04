from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, DivisionByZero, InvalidOperation
from enum import StrEnum


class BetterDirection(StrEnum):
    HIGHER = "higher"
    LOWER = "lower"


@dataclass(frozen=True)
class DefaultMinimum:
    field: str
    minimum: int | Decimal


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    column: str | None
    orderby: str | None
    qualval: str | None
    direction: BetterDirection
    default_minimums: Mapping[int, DefaultMinimum]
    numerator_column: str | None = None
    denominator_column: str | None = None
    formula_label: str | None = None

    @property
    def is_derived(self) -> bool:
        return self.numerator_column is not None

    def default_minimum(self, class_id: int) -> DefaultMinimum:
        try:
            return self.default_minimums[class_id]
        except KeyError as error:
            raise ValueError(
                f"metric {self.key!r} has no default minimum for class {class_id}"
            ) from error

    def value_from_row(self, row: Mapping[str, object]) -> Decimal | None:
        if not self.is_derived:
            if self.column is None:
                raise ValueError(f"metric {self.key!r} has no column")
            return _decimal_or_none(row.get(self.column))
        assert self.numerator_column is not None
        assert self.denominator_column is not None
        numerator = _decimal_or_none(row.get(self.numerator_column))
        denominator = _decimal_or_none(row.get(self.denominator_column))
        if denominator is None and self.denominator_column == "Outs":
            innings = _decimal_or_none(row.get("Inns"))
            not_outs = _decimal_or_none(row.get("NO"))
            if innings is not None and not_outs is not None:
                denominator = innings - not_outs
        if numerator is None or denominator in {None, Decimal(0)}:
            return None
        try:
            return numerator / denominator
        except DivisionByZero, InvalidOperation:
            return None

    def compare(self, left: Decimal | None, right: Decimal | None) -> int:
        if left == right:
            return 0
        if left is None:
            return -1
        if right is None:
            return 1
        if self.direction == BetterDirection.HIGHER:
            return 1 if left > right else -1
        return 1 if left < right else -1

    def better_than(self, left: Decimal | None, right: Decimal | None) -> bool:
        return self.compare(left, right) > 0

    def tied(self, left: Decimal | None, right: Decimal | None) -> bool:
        return self.compare(left, right) == 0


_FORMATS = (1, 2, 3, 6, 11)


def _mins(field: str, values: Mapping[int, int | Decimal]) -> dict[int, DefaultMinimum]:
    missing = set(_FORMATS) - set(values)
    if missing:
        raise ValueError(f"default minimums missing classes {sorted(missing)}")
    return {class_id: DefaultMinimum(field, value) for class_id, value in values.items()}


BATTING_METRICS: dict[str, Metric] = {
    "runs": Metric(
        "runs",
        "runs",
        "Runs",
        "runs",
        "runs",
        BetterDirection.HIGHER,
        _mins("runs", {1: 1000, 2: 500, 3: 250, 6: 500, 11: 1500}),
    ),
    "average": Metric(
        "average",
        "batting average",
        "Ave",
        "batting_average",
        "batting_average",
        BetterDirection.HIGHER,
        _mins("innings", {1: 20, 2: 20, 3: 20, 6: 30, 11: 30}),
    ),
    "strike_rate": Metric(
        "strike_rate",
        "strike rate",
        "SR",
        "batting_strike_rate",
        "batting_strike_rate",
        BetterDirection.HIGHER,
        _mins("balls_faced", {1: 500, 2: 500, 3: 250, 6: 500, 11: 1000}),
    ),
    "hundreds": Metric(
        "hundreds",
        "hundreds",
        "100",
        "hundreds",
        "hundreds",
        BetterDirection.HIGHER,
        _mins("hundreds", {1: 5, 2: 5, 3: 1, 6: 1, 11: 10}),
    ),
    "fifty_plus": Metric(
        "fifty_plus",
        "fifty-plus scores",
        "50",
        "fifty_plus",
        "fifty_plus",
        BetterDirection.HIGHER,
        _mins("fifty_plus", {1: 10, 2: 10, 3: 5, 6: 10, 11: 20}),
    ),
    "innings_per_hundred": Metric(
        "innings_per_hundred",
        "innings per hundred",
        None,
        None,
        None,
        BetterDirection.LOWER,
        _mins("hundreds", {1: 5, 2: 5, 3: 1, 6: 3, 11: 10}),
        numerator_column="Inns",
        denominator_column="100",
        formula_label="innings ÷ hundreds",
    ),
    "innings_per_fifty_plus": Metric(
        "innings_per_fifty_plus",
        "innings per fifty-plus score",
        None,
        None,
        None,
        BetterDirection.LOWER,
        _mins("fifty_plus", {1: 10, 2: 10, 3: 5, 6: 10, 11: 20}),
        numerator_column="Inns",
        denominator_column="50",
        formula_label="innings ÷ fifty-plus scores",
    ),
    "balls_per_dismissal": Metric(
        "balls_per_dismissal",
        "balls per dismissal",
        None,
        None,
        None,
        BetterDirection.HIGHER,
        _mins("outs", {1: 20, 2: 20, 3: 15, 6: 25, 11: 40}),
        numerator_column="BF",
        denominator_column="Outs",
        formula_label="balls faced ÷ dismissals",
    ),
}


def batting_metric(key: str) -> Metric:
    try:
        return BATTING_METRICS[key]
    except KeyError as error:
        raise ValueError(f"unknown batting metric {key!r}") from error


def rank_key(metric: Metric, value: Decimal | None) -> tuple[int, Decimal]:
    if value is None:
        return (1, Decimal(0))
    ranked = -value if metric.direction == BetterDirection.HIGHER else value
    return (0, ranked)


def _decimal_or_none(value: object) -> Decimal | None:
    if value is None or value == "" or value == "-":
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    try:
        return Decimal(str(value).replace(",", ""))
    except InvalidOperation:
        return None
