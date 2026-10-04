from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from crickey.fetcher import Fetcher, Freshness
from crickey.parsers import parse_results_page
from crickey.settings import Settings

LIVE_STAT_TYPE_URLS = {
    "batting": "https://stats.cricinfo.com/ci/engine/stats/index.html?class=1;orderby=runs;size=10;template=results;type=batting",
    "bowling": "https://stats.cricinfo.com/ci/engine/stats/index.html?class=1;orderby=wickets;size=10;template=results;type=bowling",
    "fielding": "https://stats.cricinfo.com/ci/engine/stats/index.html?class=1;orderby=dismissals;size=10;template=results;type=fielding",
    "allround": "https://stats.cricinfo.com/ci/engine/stats/index.html?class=1;orderby=allround_average;size=10;template=results;type=allround",
    "fow": "https://stats.cricinfo.com/ci/engine/stats/index.html?class=1;orderby=fow_runs;size=10;template=results;type=fow",
    "team": "https://stats.cricinfo.com/ci/engine/stats/index.html?class=1;orderby=won;size=10;template=results;type=team",
    "aggregate": "https://stats.cricinfo.com/ci/engine/stats/index.html?class=1;orderby=runs;size=10;template=results;type=aggregate",
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
            for stat_type, url in LIVE_STAT_TYPE_URLS.items():
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
