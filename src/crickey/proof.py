from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from crickey.fetcher import Freshness, freshness_from_end_date
from crickey.metrics import Metric
from crickey.parsers import parse_results_page
from crickey.query import Qualification, ResolvedPeriod, StatsguruQuery


@dataclass(frozen=True)
class Threshold:
    metric: Metric
    minimum: Decimal | int


@dataclass(frozen=True)
class ProofLink:
    label: str
    url: str
    confirmed: bool
    fetched: bool
    formula: str | None = None
    row_count: int | None = None


async def build_proof_link(
    query: StatsguruQuery,
    *,
    thresholds: tuple[Threshold, ...] = (),
    expected_player_ids: tuple[int, ...] = (),
    formula: str | None = None,
    call=None,
    as_of: date | None = None,
) -> ProofLink:
    as_of = date.today() if as_of is None else as_of
    if _can_express(query, thresholds):
        qualifications = query.qualifications + tuple(
            Qualification(field=threshold.metric.qualval or "", minimum=threshold.minimum)
            for threshold in thresholds
        )
        proof_query = query.model_copy(update={"qualifications": qualifications})
        url = proof_query.results_url(as_of=as_of)
        if call is None:
            raise ValueError("a fetch call is required to confirm an expressible proof link")
        html = await call.fetch(url, freshness=_freshness_for_query(proof_query, as_of))
        page = parse_results_page(html)
        actual_ids = tuple(
            int(value) for value in page.table.get("player_id", []) if value is not None
        )
        confirmed = tuple(actual_ids) == tuple(expected_player_ids) if expected_player_ids else True
        return ProofLink(
            label="Confirmed Statsguru results",
            url=url,
            confirmed=confirmed,
            fetched=True,
            row_count=len(actual_ids),
        )
    return ProofLink(
        label="Input Statsguru table",
        url=query.results_url(as_of=as_of),
        confirmed=False,
        fetched=False,
        formula=formula or _formula_text(thresholds),
    )


def _can_express(query: StatsguruQuery, thresholds: tuple[Threshold, ...]) -> bool:
    return (
        bool(thresholds)
        and len(query.qualifications) + len(thresholds) <= 3
        and all(threshold.metric.qualval for threshold in thresholds)
    )


def _formula_text(thresholds: tuple[Threshold, ...]) -> str | None:
    labels = [
        threshold.metric.formula_label for threshold in thresholds if threshold.metric.formula_label
    ]
    return "; ".join(labels) if labels else None


def _freshness_for_query(query: StatsguruQuery, as_of: date) -> Freshness:
    if isinstance(query.period, ResolvedPeriod):
        return freshness_from_end_date(query.period.end, today=as_of)
    return freshness_from_end_date(as_of, today=as_of)
