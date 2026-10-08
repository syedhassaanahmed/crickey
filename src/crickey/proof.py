from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from crickey.fetcher import Freshness, freshness_from_end_date
from crickey.metrics import BetterDirection, Metric
from crickey.parsers import parse_results_page
from crickey.parsers.common import StatsguruParseError
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
    page_allowance: int | None = None,
    fallback_reason: str | None = None,
) -> ProofLink:
    as_of = date.today() if as_of is None else as_of
    fallback_formula = formula or _formula_text(thresholds)
    if _can_express(query, thresholds):
        if page_allowance is not None and page_allowance < 1:
            return _fallback_link(
                query,
                as_of=as_of,
                formula=fallback_formula,
                reason="confirmation would exceed the per-call page limit",
            )
        if not expected_player_ids:
            return _fallback_link(
                query,
                as_of=as_of,
                formula=fallback_formula,
                reason="confirmation needs expected player IDs",
            )
        qualifications = _proof_qualifications(query, thresholds)
        proof_query = query.model_copy(update={"qualifications": qualifications})
        url = proof_query.results_url(as_of=as_of)
        if call is None:
            raise ValueError("a fetch call is required to confirm an expressible proof link")
        try:
            page = parse_results_page(
                await call.fetch(url, freshness=_freshness_for_query(proof_query, as_of))
            )
            pages = [page]
            max_pages = getattr(getattr(call, "_fetcher", None), "settings", None)
            max_pages = getattr(max_pages, "max_pages", page.totals.pages or 1)
            if page_allowance is not None:
                max_pages = min(max_pages, page_allowance)
            page_count = page.totals.pages or 1
            if page_count > max_pages:
                return _fallback_link(
                    query,
                    as_of=as_of,
                    formula=fallback_formula,
                    reason=(
                        f"confirmation needs {page_count} proof pages, "
                        f"over the {max_pages}-page limit"
                    ),
                    row_count=0 if page.no_records else page.totals.total,
                )
            for page_number in range((page.totals.page or 1) + 1, page_count + 1):
                page_query = proof_query.model_copy(update={"page": page_number})
                pages.append(
                    parse_results_page(
                        await call.fetch(
                            page_query.results_url(as_of=as_of),
                            freshness=_freshness_for_query(proof_query, as_of),
                        )
                    )
                )
        except StatsguruParseError as error:
            return _fallback_link(
                query,
                as_of=as_of,
                formula=fallback_formula,
                reason=f"confirmation page could not be parsed: {error}",
            )
        actual_ids = tuple(
            int(value)
            for parsed in pages
            for value in parsed.table.get("player_id", [])
            if value is not None
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
                reason="confirmation did not match the expected player IDs",
                row_count=0 if page.no_records else page.totals.total,
            )
        return ProofLink(
            label=f"Confirmed Statsguru results: {proof_query.label(as_of=as_of)}",
            url=url,
            confirmed=True,
            fetched=True,
            row_count=page.totals.total,
        )
    return _fallback_link(query, as_of=as_of, formula=fallback_formula, reason=fallback_reason)


def _can_express(query: StatsguruQuery, thresholds: tuple[Threshold, ...]) -> bool:
    return (
        bool(thresholds)
        and all(threshold.metric.qualval for threshold in thresholds)
        and len(_proof_qualifications(query, thresholds)) <= 3
    )


def _proof_qualifications(
    query: StatsguruQuery, thresholds: tuple[Threshold, ...]
) -> tuple[Qualification, ...]:
    # Statsguru misreads a field repeated across qualifications (R2), so a threshold on a field
    # the query already qualifies, such as a count comparison's own floor, tightens that one.
    qualifications = list(query.qualifications)
    for threshold in thresholds:
        extra = _threshold_qualification(threshold)
        index = next(
            (i for i, current in enumerate(qualifications) if current.field == extra.field), None
        )
        if index is None:
            qualifications.append(extra)
        else:
            qualifications[index] = _tightened(qualifications[index], extra)
    return tuple(qualifications)


def _tightened(current: Qualification, extra: Qualification) -> Qualification:
    minimums = [value for value in (current.minimum, extra.minimum) if value is not None]
    maximums = [value for value in (current.maximum, extra.maximum) if value is not None]
    return Qualification(
        field=current.field,
        minimum=max(minimums) if minimums else None,
        maximum=min(maximums) if maximums else None,
    )


def _threshold_qualification(threshold: Threshold) -> Qualification:
    if threshold.metric.direction == BetterDirection.HIGHER:
        return Qualification(field=threshold.metric.qualval or "", minimum=threshold.minimum)
    return Qualification(
        field=threshold.metric.qualval or "",
        maximum=_inclusive_display_max(threshold),
    )


def _inclusive_display_max(threshold: Threshold) -> Decimal | int:
    value = threshold.minimum
    if isinstance(value, int):
        return value
    precision = threshold.metric.proof_precision
    if precision is None:
        precision = max(-value.as_tuple().exponent, 0)
    unit = Decimal(1).scaleb(-precision)
    return value + unit - Decimal("0.0001")


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
