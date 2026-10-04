from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from crickey.fetcher import (
    USER_AGENT,
    BlockedError,
    Fetcher,
    FetchResponse,
    FetchTimeoutError,
    Freshness,
    MemoryPageSource,
    RefusedUrlError,
    TooBroadError,
    UnavailableUrlError,
    freshness_from_end_date,
)
from crickey.settings import Settings

URL = "https://stats.cricinfo.com/ci/engine/stats/index.html?class=1;type=batting"
OTHER_URL = "https://stats.cricinfo.com/ci/engine/stats/index.html?class=2;type=batting"
STATS_PAGE = '<html><title>Statsguru</title><table class="engineTable"></table></html>'


class FakeClock:
    def __init__(self) -> None:
        self.monotonic_time = 0.0
        self.wall_time = datetime(2026, 10, 4, 8, 0, tzinfo=UTC)
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.monotonic_time

    def now(self) -> datetime:
        return self.wall_time + timedelta(seconds=self.monotonic_time)

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.monotonic_time += seconds


class SequenceSource:
    def __init__(self, responses: list[FetchResponse | Exception]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, Mapping[str, str]]] = []

    async def get(self, url: str, headers: Mapping[str, str]) -> FetchResponse:
        self.requests.append((url, headers))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def settings(**overrides: object) -> Settings:
    values = {
        "min_interval": timedelta(seconds=2),
        "max_retries": 3,
        "block_pauses": (timedelta(seconds=10), timedelta(seconds=20)),
        "max_pages": 2,
        "cache_max_mb": 1,
        "recent_ttl": timedelta(seconds=30),
        "port": 8765,
        "in_container": False,
    }
    values.update(overrides)
    return Settings(**values)


def run(coro: Awaitable[object]) -> object:
    return asyncio.run(coro)


def capture(events: list[tuple[float, str]]) -> Callable[[float, str], None]:
    def progress(seconds: float, reason: str) -> None:
        events.append((seconds, reason))

    return progress


def ok(url: str = URL, text: str = STATS_PAGE) -> FetchResponse:
    return FetchResponse(url=url, status_code=200, headers={}, text=text)


def response(
    status: int, *, headers: Mapping[str, str] | None = None, text: str = STATS_PAGE
) -> FetchResponse:
    return FetchResponse(url=URL, status_code=status, headers=headers or {}, text=text)


def test_refuses_non_statsguru_hosts_before_request() -> None:
    source = SequenceSource([])
    fetcher = Fetcher(settings(), clock=FakeClock(), page_source=source)

    with pytest.raises(RefusedUrlError, match="only fetches"):
        run(fetcher.fetch("https://www.cricinfo.com/", freshness=Freshness.RECENT, budget=10))

    assert source.requests == []


def test_user_agent_constant_is_sent_and_respx_sees_no_default_network() -> None:
    clock = FakeClock()
    with respx.mock(assert_all_called=True) as router:
        route = router.get(URL).mock(return_value=httpx.Response(200, text=STATS_PAGE))
        fetcher = Fetcher(settings(), clock=clock, jitter=lambda base: 0)

        assert run(fetcher.fetch(URL, freshness=Freshness.RECENT, budget=10)) == STATS_PAGE

    assert route.calls.call_count == 1
    assert route.calls[0].request.headers["User-Agent"] == USER_AGENT


def test_rate_limiter_spaces_all_requests_and_reports_progress() -> None:
    clock = FakeClock()
    events: list[tuple[float, str]] = []
    source = SequenceSource([ok(), ok(OTHER_URL)])
    fetcher = Fetcher(settings(min_interval=timedelta(seconds=3)), clock=clock, page_source=source)

    progress = capture(events)
    run(fetcher.fetch(URL, freshness=Freshness.RECENT, budget=20, progress=progress))
    run(fetcher.fetch(OTHER_URL, freshness=Freshness.RECENT, budget=20, progress=progress))

    assert clock.sleeps == [3.0]
    assert events == [(3.0, "spacing requests politely")]
    assert [request[0] for request in source.requests] == [URL, OTHER_URL]


def test_retries_timeout_and_server_error_with_exact_waits() -> None:
    clock = FakeClock()
    events: list[tuple[float, str]] = []
    source = SequenceSource([httpx.ConnectError("boom"), response(503), ok()])
    fetcher = Fetcher(settings(), clock=clock, page_source=source, jitter=lambda base: 0)

    progress = capture(events)
    assert (
        run(fetcher.fetch(URL, freshness=Freshness.RECENT, budget=100, progress=progress))
        == STATS_PAGE
    )

    assert clock.sleeps == [15.0, 30.0]
    assert events == [
        (15.0, "waiting before retrying Statsguru"),
        (30.0, "waiting before retrying Statsguru"),
    ]
    assert len(source.requests) == 3


def test_retry_after_seconds_sets_process_not_before_and_budget_message() -> None:
    clock = FakeClock()
    source = SequenceSource([response(429, headers={"Retry-After": "120"})])
    fetcher = Fetcher(settings(), clock=clock, page_source=source, jitter=lambda base: 0)

    with pytest.raises(FetchTimeoutError, match="try again after 08:02"):
        run(fetcher.fetch(URL, freshness=Freshness.RECENT, budget=20))

    assert len(source.requests) == 1
    with pytest.raises(FetchTimeoutError, match="try again after 08:02"):
        run(fetcher.fetch(OTHER_URL, freshness=Freshness.RECENT, budget=20))
    assert len(source.requests) == 1


def test_retry_after_http_date_waits_when_budget_allows() -> None:
    clock = FakeClock()
    events: list[tuple[float, str]] = []
    date_header = "Sun, 04 Oct 2026 08:00:45 GMT"
    source = SequenceSource([response(429, headers={"Retry-After": date_header}), ok()])
    fetcher = Fetcher(settings(), clock=clock, page_source=source, jitter=lambda base: 0)

    progress = capture(events)
    assert (
        run(fetcher.fetch(URL, freshness=Freshness.RECENT, budget=100, progress=progress))
        == STATS_PAGE
    )

    assert clock.sleeps == [45.0]
    assert events[0] == (45.0, "waiting for Statsguru's Retry-After time")


def test_jitter_is_bounded_by_ten_percent_when_default_jitter_is_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    monkeypatch.setattr("crickey.fetcher.random.uniform", lambda low, high: high)
    source = SequenceSource([response(500), ok()])
    fetcher = Fetcher(settings(), clock=clock, page_source=source)

    run(fetcher.fetch(URL, freshness=Freshness.RECENT, budget=100))

    assert clock.sleeps[0] == 16.5


def test_retry_stops_when_next_wait_would_not_fit_budget() -> None:
    source = SequenceSource([response(500)])
    fetcher = Fetcher(settings(), clock=FakeClock(), page_source=source, jitter=lambda base: 0)

    with pytest.raises(FetchTimeoutError, match="Not enough time left to retry"):
        run(fetcher.fetch(URL, freshness=Freshness.RECENT, budget=10))

    assert len(source.requests) == 1


def test_unavailable_urls_are_not_requested_again() -> None:
    source = SequenceSource([response(404)])
    fetcher = Fetcher(settings(), clock=FakeClock(), page_source=source)

    with pytest.raises(UnavailableUrlError, match="unavailable"):
        run(fetcher.fetch(URL, freshness=Freshness.RECENT, budget=10))
    with pytest.raises(UnavailableUrlError, match="unavailable earlier"):
        run(fetcher.fetch(URL, freshness=Freshness.RECENT, budget=10))

    assert len(source.requests) == 1


def test_block_pause_grows_caps_resets_and_cached_pages_still_work() -> None:
    clock = FakeClock()
    source = SequenceSource(
        [
            ok(text="cached Statsguru engineTable"),
            response(403),
            response(403),
            ok(OTHER_URL),
        ]
    )
    fetcher = Fetcher(settings(), clock=clock, page_source=source)

    assert (
        run(fetcher.fetch(URL, freshness=Freshness.SETTLED, budget=10))
        == "cached Statsguru engineTable"
    )
    with pytest.raises(BlockedError, match="blocked"):
        run(fetcher.fetch(OTHER_URL, freshness=Freshness.RECENT, budget=20))
    assert (
        run(fetcher.fetch(URL, freshness=Freshness.SETTLED, budget=10))
        == "cached Statsguru engineTable"
    )
    with pytest.raises(BlockedError, match="paused until 08:00"):
        run(fetcher.fetch(OTHER_URL, freshness=Freshness.RECENT, budget=20))

    clock.monotonic_time = 12
    with pytest.raises(BlockedError):
        run(fetcher.fetch(OTHER_URL, freshness=Freshness.RECENT, budget=20))
    with pytest.raises(BlockedError, match="paused until 08:00"):
        run(fetcher.fetch(OTHER_URL, freshness=Freshness.RECENT, budget=20))

    clock.monotonic_time = 32
    assert run(fetcher.fetch(OTHER_URL, freshness=Freshness.RECENT, budget=30)) == STATS_PAGE
    assert source.requests[-1][0] == OTHER_URL


def test_challenge_detection_requires_challenge_marker_without_statsguru_marker() -> None:
    challenge = "<html><title>Access denied</title><p>enable JavaScript challenge</p></html>"
    source = SequenceSource([response(200, text=challenge)])
    fetcher = Fetcher(settings(), clock=FakeClock(), page_source=source)

    with pytest.raises(BlockedError):
        run(fetcher.fetch(URL, freshness=Freshness.RECENT, budget=10))


def test_redirects_are_followed_only_within_statsguru() -> None:
    clock = FakeClock()
    source = SequenceSource(
        [
            FetchResponse(URL, 302, {"Location": OTHER_URL}, ""),
            ok(OTHER_URL, text="redirected Statsguru engineTable"),
        ]
    )
    fetcher = Fetcher(settings(), clock=clock, page_source=source)

    assert (
        run(fetcher.fetch(URL, freshness=Freshness.RECENT, budget=10))
        == "redirected Statsguru engineTable"
    )
    assert clock.sleeps == [2.0]

    bad = SequenceSource([FetchResponse(URL, 302, {"Location": "https://www.cricinfo.com/"}, "")])
    blocked = Fetcher(settings(), clock=FakeClock(), page_source=bad)
    with pytest.raises(RefusedUrlError, match="only fetches"):
        run(blocked.fetch(URL, freshness=Freshness.RECENT, budget=10))


def test_cache_freshness_rules_and_force_refetch_for_lookup() -> None:
    clock = FakeClock()
    source = SequenceSource(
        [
            ok(text="recent 1 Statsguru"),
            ok(text="recent 2 Statsguru"),
            ok(text="lookup 2 Statsguru"),
        ]
    )
    fetcher = Fetcher(settings(recent_ttl=timedelta(seconds=5)), clock=clock, page_source=source)

    assert run(fetcher.fetch(URL, freshness=Freshness.RECENT, budget=10)) == "recent 1 Statsguru"
    assert run(fetcher.fetch(URL, freshness=Freshness.RECENT, budget=10)) == "recent 1 Statsguru"
    clock.monotonic_time = 6
    assert run(fetcher.fetch(URL, freshness=Freshness.RECENT, budget=20)) == "recent 2 Statsguru"

    assert (
        run(fetcher.fetch(OTHER_URL, freshness=Freshness.LOOKUP, budget=20)) == "lookup 2 Statsguru"
    )
    clock.monotonic_time = 1_000
    assert (
        run(fetcher.fetch(OTHER_URL, freshness=Freshness.LOOKUP, budget=20)) == "lookup 2 Statsguru"
    )
    source.responses.append(ok(OTHER_URL, text="lookup 3 Statsguru"))
    assert (
        run(fetcher.fetch(OTHER_URL, freshness=Freshness.LOOKUP, budget=20, force_refetch=True))
        == "lookup 3 Statsguru"
    )


def test_settled_or_recent_helper_uses_query_end_date() -> None:
    today = datetime(2026, 10, 4, tzinfo=UTC).date()

    assert freshness_from_end_date(None, today) is Freshness.RECENT
    assert freshness_from_end_date(today - timedelta(days=7), today) is Freshness.RECENT
    assert freshness_from_end_date(today - timedelta(days=8), today) is Freshness.SETTLED


def test_cache_size_cap_evicts_least_recently_used_page() -> None:
    clock = FakeClock()
    source = SequenceSource(
        [
            ok(URL, text="A" * 900 + " Statsguru"),
            ok(OTHER_URL, text="B" * 900 + " Statsguru"),
            ok(URL, text="A refetched Statsguru"),
        ]
    )
    fetcher = Fetcher(settings(cache_max_mb=1), clock=clock, page_source=source)
    fetcher.cache._max_bytes = 40

    assert "A" in run(fetcher.fetch(URL, freshness=Freshness.SETTLED, budget=10))
    assert "B" in run(fetcher.fetch(OTHER_URL, freshness=Freshness.SETTLED, budget=20))
    assert (
        run(fetcher.fetch(URL, freshness=Freshness.SETTLED, budget=20)) == "A refetched Statsguru"
    )

    assert [request[0] for request in source.requests] == [URL, OTHER_URL, URL]


def test_page_cap_refuses_after_first_page_and_keeps_it_cached() -> None:
    first = "Statsguru engineTable <b>Page 1 of 3</b>"
    source = SequenceSource([ok(text=first)])
    fetcher = Fetcher(settings(max_pages=2), clock=FakeClock(), page_source=source)

    def page_url(page: int) -> str:
        return f"{URL};page={page}"

    with pytest.raises(TooBroadError, match="needs 3 pages.*limit is 2"):
        run(fetcher.fetch_pages(URL, page_url, freshness=Freshness.RECENT, budget=10))
    assert run(fetcher.fetch(URL, freshness=Freshness.RECENT, budget=10)) == first
    assert len(source.requests) == 1


def test_test_hook_uses_same_retry_cache_and_rate_limiter() -> None:
    clock = FakeClock()
    source = MemoryPageSource(
        {URL: STATS_PAGE, OTHER_URL: STATS_PAGE.replace("Statsguru", "Statsguru 2")}
    )
    fetcher = Fetcher(settings(min_interval=timedelta(seconds=4)), clock=clock, page_source=source)

    assert run(fetcher.fetch(URL, freshness=Freshness.RECENT, budget=10)) == STATS_PAGE
    assert run(fetcher.fetch(URL, freshness=Freshness.RECENT, budget=10)) == STATS_PAGE
    assert run(fetcher.fetch(OTHER_URL, freshness=Freshness.RECENT, budget=10)).startswith("<html>")

    assert source.requests == [URL, OTHER_URL]
    assert clock.sleeps == [4.0]


@pytest.mark.live
def test_live_statsguru_smoke_fetches_one_page() -> None:
    fetcher = Fetcher(
        Settings(max_retries=0, min_interval=timedelta(seconds=15)),
        jitter=lambda base: 0,
    )

    async def smoke() -> str:
        try:
            return await fetcher.fetch(URL, freshness=Freshness.RECENT, budget=60)
        finally:
            await fetcher.aclose()

    page = asyncio.run(smoke())

    assert "Statsguru" in page or "engineTable" in page
