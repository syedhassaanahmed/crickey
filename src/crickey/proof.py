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
    fallback_formula = formula or _formula_text(thresholds)
    if _can_express(query, thresholds):
        if not expected_player_ids:
            return _fallback_link(
                query,
                as_of=as_of,
                formula=fallback_formula,
                reason="confirmation needs expected player IDs",
            )
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
        confirmed = (
            not page.no_records
            and set(actual_ids) == set(expected_player_ids)
            and len(actual_ids) == len(expected_player_ids)
            and page.totals.total == len(expected_player_ids)
        )
        if not confirmed:
            return _fallback_link(
                query,
                as_of=as_of,
                formula=fallback_formula,
                reason="confirmation did not match Statsguru rows",
                row_count=page.totals.total,
            )
        return ProofLink(
            label=f"Confirmed Statsguru results: {proof_query.label(as_of=as_of)}",
            url=url,
            confirmed=True,
            fetched=True,
            row_count=page.totals.total,
        )
    return _fallback_link(query, as_of=as_of, formula=fallback_formula)


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


def _fallback_link(
    query: StatsguruQuery,
    *,
    as_of: date,
    formula: str | None,
    reason: str | None = None,
    row_count: int | None = None,
) -> ProofLink:
    suffix = f" ({reason})" if reason else ""
    return ProofLink(
        label=f"Input Statsguru table: {query.label(as_of=as_of)}{suffix}",
        url=query.results_url(as_of=as_of),
        confirmed=False,
        fetched=False,
        formula=formula,
        row_count=row_count,
    )


def _freshness_for_query(query: StatsguruQuery, as_of: date) -> Freshness:
    if isinstance(query.period, ResolvedPeriod):
        return freshness_from_end_date(query.period.end, today=as_of)
    return freshness_from_end_date(as_of, today=as_of)
