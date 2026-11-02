from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date

import pytest
from mcp import Client
from stat_type_cases import REPRESENTATIVE_STAT_QUERIES, representative_query_payload

from crickey.fetcher import Fetcher, Freshness
from crickey.parsers import parse_results_page
from crickey.query import StatsguruQuery
from crickey.server import create_server
from crickey.settings import Settings

pytestmark = [pytest.mark.live, pytest.mark.anyio]

# Answers that can't change, from research.md's example data: a player search, then golden
# questions 1, 2, 5, 8 (Dravid) and 10. Between them they read the player search, player pages
# with a split, a filter form and results pages, each through the tool that uses it.
KNOWN_ANSWERS = (
    pytest.param("find_player", {"name": "Dravid", "format": "Test"}, ("28114",), id="search"),
    pytest.param(
        "leaderboard",
        {"format": "ODI", "metric": "innings_per_hundred", "period": "2000 to 2009", "minimum": 10},
        ("13.72", "Tendulkar", "9.86"),
        id="golden-1",
    ),
    pytest.param(
        "better_than_player",
        {"player_id": 35320, "format": "Test", "metrics": ["innings_per_hundred"]},
        ("6.45", "Pujara", "4.8", "Azharuddin", "6.44"),
        id="golden-2",
    ),
    pytest.param(
        "player_record",
        {"player_id": 35320, "format": "ODI", "trophy": "World Cup"},
        ("2278", "56.95"),
        id="golden-5",
    ),
    pytest.param(
        "player_record",
        {"player_id": 28114, "format": "Test", "split_by": "host"},
        ("at least one hundred in all 10 host countries",),
        id="golden-8",
    ),
    pytest.param(
        "series_count",
        {
            "format": "ODI",
            "number_of_teams": "3+",
            "period": "1990 to 1999",
            "host_country": "Pakistan",
        },
        ("3 ODI series of 3+ teams were played in Pakistan", "Wills Triangular Series, 1994/95"),
        id="golden-10",
    ),
)


@pytest.fixture(scope="module")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="module")
async def fetcher() -> AsyncIterator[Fetcher]:
    # One fetcher for every live test, so D9's spacing and D11's block pause cover them all.
    fetcher = Fetcher(Settings())
    try:
        yield fetcher
    finally:
        await fetcher.aclose()


def _live_urls() -> dict[str, str]:
    return {
        stat_type: StatsguruQuery(**representative_query_payload(1, stat_type)).results_url(
            as_of=date(2026, 10, 4)
        )
        for stat_type in REPRESENTATIVE_STAT_QUERIES
    }


async def test_live_statsguru_results_parse_for_every_stat_type(fetcher: Fetcher) -> None:
    columns: dict[str, tuple[str, ...]] = {}
    for stat_type, url in _live_urls().items():
        parsed = parse_results_page(await fetcher.fetch(url, freshness=Freshness.RECENT, budget=90))
        assert parsed.no_records is False, stat_type
        assert parsed.totals.total and parsed.totals.total >= 1, stat_type
        assert len(parsed.table) >= 1, stat_type
        columns[stat_type] = tuple(parsed.headers)

    assert "player_id" in columns["batting"]
    assert "player_id" in columns["bowling"]
    assert "player_id" in columns["fielding"]
    assert "player_id" in columns["allround"]
    assert "player_id" not in columns["fow"]
    assert "player_id" not in columns["team"]
    assert "player_id" not in columns["aggregate"]


@pytest.mark.parametrize(("tool", "arguments", "facts"), KNOWN_ANSWERS)
async def test_live_tools_give_answers_that_cannot_change(
    fetcher: Fetcher, tool: str, arguments: dict[str, object], facts: tuple[str, ...]
) -> None:
    async with Client(create_server(fetcher.settings, fetcher=fetcher)) as client:
        result = await client.call_tool(tool, arguments)

    text = result.content[0].text.replace(",", "")
    assert not result.is_error, text
    assert [fact for fact in facts if fact.replace(",", "") not in text] == [], text
