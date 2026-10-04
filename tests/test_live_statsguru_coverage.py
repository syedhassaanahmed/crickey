from __future__ import annotations

import asyncio
from datetime import date, timedelta

import pytest
from stat_type_cases import REPRESENTATIVE_STAT_QUERIES, representative_query_payload

from crickey.fetcher import Fetcher, Freshness
from crickey.parsers import parse_results_page
from crickey.query import StatsguruQuery
from crickey.settings import Settings


def _live_urls() -> dict[str, str]:
    return {
        stat_type: StatsguruQuery(**representative_query_payload(1, stat_type)).results_url(
            as_of=date(2026, 10, 4)
        )
        for stat_type in REPRESENTATIVE_STAT_QUERIES
    }


@pytest.mark.live
def test_live_statsguru_results_parse_for_every_stat_type() -> None:
    fetcher = Fetcher(
        Settings(max_retries=0, min_interval=timedelta(seconds=15)),
        jitter=lambda base: 0,
    )

    async def fetch_all() -> dict[str, tuple[str, ...]]:
        columns: dict[str, tuple[str, ...]] = {}
        try:
            await asyncio.sleep(15)
            for stat_type, url in _live_urls().items():
                html = await fetcher.fetch(url, freshness=Freshness.RECENT, budget=90)
                parsed = parse_results_page(html)
                assert parsed.no_records is False, stat_type
                assert parsed.totals.total and parsed.totals.total >= 1, stat_type
                assert len(parsed.table) >= 1, stat_type
                columns[stat_type] = tuple(parsed.headers)
        finally:
            await fetcher.aclose()
        return columns

    columns = asyncio.run(fetch_all())

    assert "player_id" in columns["batting"]
    assert "player_id" in columns["bowling"]
    assert "player_id" in columns["fielding"]
    assert "player_id" in columns["allround"]
    assert "player_id" not in columns["fow"]
    assert "player_id" not in columns["team"]
    assert "player_id" not in columns["aggregate"]
