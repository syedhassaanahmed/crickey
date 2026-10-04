from __future__ import annotations

# ruff: noqa: E501
import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from mcp import Client

from crickey.fetcher import Fetcher, MemoryPageSource
from crickey.query import Qualification, StatsguruQuery
from crickey.server import SERVER_INSTRUCTIONS, create_server
from crickey.settings import Settings

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeClock:
    def __init__(self) -> None:
        self.monotonic_time = 0.0
        self.wall_time = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.monotonic_time

    def now(self) -> datetime:
        return self.wall_time + timedelta(seconds=self.monotonic_time)

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.monotonic_time += seconds
        await asyncio.sleep(0)


def settings(**overrides: object) -> Settings:
    values = {
        "min_interval": timedelta(seconds=0),
        "max_retries": 0,
        "block_pauses": (timedelta(seconds=10),),
        "max_pages": 4,
        "cache_max_mb": 1,
        "recent_ttl": timedelta(seconds=30),
        "port": 8765,
        "in_container": False,
    }
    values.update(overrides)
    return Settings(**values)


def player_search_url(search: str) -> str:
    return f"https://stats.cricinfo.com/ci/engine/stats/analysis.html?search={search};template=analysis"


def results_url(query: StatsguruQuery, *, page: int | None = None) -> str:
    if page is not None:
        query = query.model_copy(update={"page": page})
    return query.results_url(as_of=FakeClock().now().date())


def result_page(rows: list[str], *, page: int = 1, pages: int = 1, total: int | None = None) -> str:
    total = len(rows) if total is None else total
    start = 1 if not rows else (page - 1) * 200 + 1
    end = start + len(rows) - 1 if rows else 0
    return f"""
    <html><body>
    <table class="engineTable"><caption>Overall figures</caption>
    <tr><th>Player</th><th>Span</th><th>Mat</th><th>Runs</th><th>HS</th><th>Ave</th><th>100</th></tr>
    {"".join(rows)}
    </table>
    <table><tr><td>Page <b>{page}</b> of <b>{pages}</b></td><td>Showing <b>{start}</b> - <b>{end}</b> of <b>{total}</b></td></tr></table>
    <table class="engineTable"><tr class="data2"><td><b>Statsguru includes the following current or recent ODIs:</b></td></tr>
    <tr class="data2"><td>Example XI v Sample XI at Testville, 1st ODI, Oct 3, 2026 [<a href="/ci/engine/match/1.html">ODI # 1</a>]</td></tr>
    </table>
    </body></html>
    """


def row(player_id: int, name: str, team: str, runs: int, ave: str = "50.00") -> str:
    return f"""
    <tr class="data1"><td><a href="/ci/content/player/{player_id}.html">{name}</a> ({team})</td><td>2015-2026</td><td>10</td><td>{runs}</td><td>101*</td><td>{ave}</td><td>1</td></tr>
    """


def source_for(pages: dict[str, str]) -> MemoryPageSource:
    return MemoryPageSource(pages)


async def call_with_source(pages: dict[str, str], settings_: Settings | None = None):
    source = source_for(pages)
    clock = FakeClock()
    fetcher = Fetcher(settings_ or settings(), clock=clock, page_source=source)
    mcp = create_server(settings_ or settings(), fetcher=fetcher)
    client = Client(mcp)
    return source, clock, client


async def test_tool_listing_has_read_only_annotations_and_instructions() -> None:
    _, _, client = await call_with_source({})

    async with client:
        tools = (await client.list_tools()).tools
        by_name = {tool.name: tool for tool in tools}

        assert set(by_name) == {"find_player", "query_stats"}
        assert by_name["find_player"].annotations.read_only_hint is True
        assert by_name["find_player"].annotations.open_world_hint is True
        assert by_name["query_stats"].annotations.read_only_hint is True
        assert by_name["find_player"].description.startswith("Example:")
        assert by_name["query_stats"].description.startswith("Example:")
        assert "answer_markdown as-is" in (client.instructions or "")
        assert "multi-row arithmetic" in (client.instructions or "")
        assert SERVER_INSTRUCTIONS == client.instructions


async def test_find_player_unique_ambiguous_and_unknown() -> None:
    html = """
    <table>
    <tr><td>Babar Azam</td><td>PAK</td><td><a href="/ci/engine/player/348144.html?class=3;type=allround">Twenty20 Internationals player</a> (2016 - 2026, 145 matches)</td></tr>
    <tr><td>Babar Hayat</td><td>HKG</td><td><a href="/ci/engine/player/539305.html?class=3;type=allround">Twenty20 Internationals player</a> (2014 - 2026, 79 matches)</td></tr>
    <tr><td>Babar Official</td><td>PAK</td><td><a href="/ci/engine/player/1.html?class=3;type=allround">Twenty20 Internationals official</a> (2020 - 2021)</td></tr>
    </table>
    """
    source, _, client = await call_with_source(
        {
            player_search_url("Babar+Azam"): html,
            player_search_url("Babar"): html,
            player_search_url("No+Such"): "<html></html>",
        }
    )

    async with client:
        unique = await client.call_tool("find_player", {"name": "Babar Azam", "format": "T20I"})
        ambiguous = await client.call_tool("find_player", {"name": "Babar", "format": "T20I"})
        unknown = await client.call_tool("find_player", {"name": "No Such", "format": "T20I"})

    assert unique.structured_content["status"] == "match"
    assert unique.structured_content["match"]["id"] == 348144
    assert unique.structured_content["match"]["country"] == ["PAK"]
    assert unique.structured_content["match"]["formats"] == [
        {
            "class": 3,
            "label": "Twenty20 Internationals player",
            "role": "player",
            "span": "2016-2026",
            "matches": 145,
        }
    ]
    assert ambiguous.structured_content["status"] == "needs_clarification"
    assert [candidate["id"] for candidate in ambiguous.structured_content["candidates"]] == [
        348144,
        539305,
    ]
    assert unknown.structured_content == {
        "status": "not_found",
        "query": "No Such",
        "candidates": [],
    }
    assert len(source.requests) == 3


async def test_query_stats_fetch_returns_exact_rows_columns_total_link_and_label() -> None:
    query = StatsguruQuery(
        **{
            "class": 2,
            "type": "batting",
            "qualifications": (Qualification(field="hundreds", minimum=10),),
            "orderby": "hundreds",
        }
    )
    url = results_url(query)
    source, _, client = await call_with_source(
        {url: result_page([row(348144, "Babar Azam", "PAK", 6626)], total=64)}
    )

    async with client:
        result = await client.call_tool(
            "query_stats",
            {
                "query": {
                    "class": 2,
                    "type": "batting",
                    "qualifications": [{"field": "hundreds", "minimum": 10}],
                    "orderby": "hundreds",
                }
            },
        )

    assert source.requests == [url]
    assert result.structured_content["columns"] == [
        "Player",
        "Span",
        "Mat",
        "Runs",
        "HS",
        "Ave",
        "100",
    ]
    assert result.structured_content["rows"] == [
        {
            "Player": "Babar Azam (PAK)",
            "Span": "2015-2026",
            "Mat": 10,
            "Runs": 6626,
            "HS": "101*",
            "Ave": "50.00",
            "100": 1,
        }
    ]
    assert result.structured_content["total"] == 64
    assert result.structured_content["link"] == url
    assert "ODI batting" in result.structured_content["label"]
    assert "at least 10 hundreds" in result.structured_content["label"]
    assert result.structured_content["freshness"].startswith(
        "Freshness: newest match Statsguru included is Example XI v Sample XI"
    )


async def test_query_stats_limit_default_maximum_and_over_limit_error() -> None:
    query = StatsguruQuery(**{"class": 2, "type": "batting"})
    url1 = results_url(query)
    url2 = results_url(query, page=2)
    rows1 = [row(index, f"Player {index}", "AAA", index) for index in range(1, 151)]
    rows2 = [row(index, f"Player {index}", "AAA", index) for index in range(151, 202)]
    source, _, client = await call_with_source(
        {
            url1: result_page(rows1, page=1, pages=2, total=201),
            url2: result_page(rows2, page=2, pages=2, total=201),
        }
    )

    async with client:
        default = await client.call_tool("query_stats", {"query": {"class": 2, "type": "batting"}})
        maximum = await client.call_tool(
            "query_stats", {"query": {"class": 2, "type": "batting"}, "limit": 200}
        )
        over = await client.call_tool(
            "query_stats", {"query": {"class": 2, "type": "batting"}, "limit": 201}
        )

    assert len(default.structured_content["rows"]) == 50
    assert len(maximum.structured_content["rows"]) == 200
    assert over.is_error is True
    assert "limit must be from 1 to 200" in over.content[0].text
    assert source.requests == [url1, url2]


async def test_query_stats_fetch_false_makes_no_request() -> None:
    source, _, client = await call_with_source({})

    async with client:
        result = await client.call_tool(
            "query_stats",
            {"query": {"class": 2, "type": "batting"}, "fetch": False},
        )

    assert source.requests == []
    assert result.structured_content["fetch"] is False
    assert result.structured_content["rows"] == []
    assert result.structured_content["columns"] == []
    assert result.structured_content["link"] == results_url(
        StatsguruQuery(**{"class": 2, "type": "batting"})
    )


async def test_query_stats_too_broad_and_validation_errors_are_clear_tool_errors() -> None:
    query = StatsguruQuery(**{"class": 2, "type": "batting"})
    source, _, client = await call_with_source(
        {results_url(query): result_page([row(1, "A", "AAA", 1)], pages=2, total=201)},
        settings(max_pages=1),
    )

    async with client:
        broad = await client.call_tool("query_stats", {"query": {"class": 2, "type": "batting"}})
        invalid = await client.call_tool(
            "query_stats", {"query": {"class": 2, "type": "batting", "view": "not-a-view"}}
        )

    assert source.requests == [results_url(query)]
    assert broad.is_error is True
    assert "too broad" in broad.content[0].text
    assert "limit is 1" in broad.content[0].text
    assert invalid.is_error is True
    assert "Invalid Statsguru query" in invalid.content[0].text
    assert "view:" in invalid.content[0].text
    assert "not-a-view" in invalid.content[0].text


async def test_progress_notifications_are_sent_during_spacing_wait() -> None:
    query = StatsguruQuery(**{"class": 2, "type": "batting"})
    url1 = results_url(query)
    url2 = results_url(query, page=2)
    source, clock, client = await call_with_source(
        {
            url1: result_page([row(1, "A", "AAA", 1)], page=1, pages=2, total=2),
            url2: result_page([row(2, "B", "BBB", 2)], page=2, pages=2, total=2),
        },
        settings(min_interval=timedelta(seconds=3)),
    )
    events: list[tuple[float, float | None, str | None]] = []

    async def progress(progress: float, total: float | None, message: str | None) -> None:
        events.append((progress, total, message))

    async with client:
        result = await client.call_tool(
            "query_stats",
            {"query": {"class": 2, "type": "batting"}, "limit": 2},
            progress_callback=progress,
        )

    assert len(result.structured_content["rows"]) == 2
    assert source.requests == [url1, url2]
    assert clock.sleeps == [3.0]
    assert events == [(0.0, None, "spacing requests politely for 3 seconds")]
