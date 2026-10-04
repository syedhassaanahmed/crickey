from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta, timezone

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
THIRD_URL = "https://stats.cricinfo.com/ci/engine/stats/index.html?class=3;type=batting"
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
        await asyncio.sleep(0)


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


class YieldingSequenceSource(SequenceSource):
    def __init__(self, responses: list[FetchResponse | Exception], clock: FakeClock) -> None:
        super().__init__(responses)
        self.clock = clock
        self.request_times: list[float] = []

    async def get(self, url: str, headers: Mapping[str, str]) -> FetchResponse:
        self.request_times.append(self.clock.monotonic())
        await asyncio.sleep(0)
        return await super().get(url, headers)


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


def test_concurrent_fetches_are_spaced_by_one_process_lock() -> None:
    async def scenario() -> tuple[list[float], list[str]]:
        clock = FakeClock()
        source = YieldingSequenceSource([ok(), ok(OTHER_URL), ok(THIRD_URL)], clock)
        fetcher = Fetcher(
            settings(min_interval=timedelta(seconds=0.5)), clock=clock, page_source=source
        )
        await asyncio.gather(
            fetcher.fetch(URL, freshness=Freshness.RECENT, budget=20),
            fetcher.fetch(OTHER_URL, freshness=Freshness.RECENT, budget=20),
            fetcher.fetch(THIRD_URL, freshness=Freshness.RECENT, budget=20),
        )
        return source.request_times, [request[0] for request in source.requests]

    request_times, urls = asyncio.run(scenario())

    assert request_times[0] == 0.0
    assert request_times[1] - request_times[0] >= 0.5
    assert request_times[2] - request_times[1] >= 0.5
    assert urls == [URL, OTHER_URL, THIRD_URL]


def test_known_not_before_fails_before_waiting_for_request_lock() -> None:
    async def scenario() -> tuple[list[tuple[float, str]], int]:
        clock = FakeClock()
        events: list[tuple[float, str]] = []
        source = SequenceSource([])
        fetcher = Fetcher(settings(), clock=clock, page_source=source)
        fetcher._not_before = 100
        await fetcher._request_lock.acquire()
        try:
            with pytest.raises(FetchTimeoutError, match=r"try again after 08:02 \(UTC\+00:00\)"):
                await fetcher.fetch(
                    URL, freshness=Freshness.RECENT, budget=30, progress=capture(events)
                )
        finally:
            fetcher._request_lock.release()
        return events, len(source.requests)

    events, request_count = asyncio.run(scenario())

    assert events == []
    assert request_count == 0


def test_waiting_for_request_lock_obeys_budget() -> None:
    async def scenario() -> tuple[list[tuple[float, str]], int, float]:
        clock = FakeClock()
        events: list[tuple[float, str]] = []
        source = SequenceSource([ok()])
        fetcher = Fetcher(settings(), clock=clock, page_source=source)
        await fetcher._request_lock.acquire()
        try:
            with pytest.raises(FetchTimeoutError, match="wait for another Statsguru request"):
                await fetcher.fetch(
                    URL, freshness=Freshness.RECENT, budget=0.3, progress=capture(events)
                )
        finally:
            fetcher._request_lock.release()
        return events, len(source.requests), clock.monotonic()

    events, request_count, elapsed = asyncio.run(scenario())

    assert events == [(0.0, "waiting for another Statsguru request")]
    assert request_count == 0
    assert elapsed == pytest.approx(0.3)


def test_request_lock_progress_reports_queue_start_and_periodic_waits() -> None:
    async def scenario() -> tuple[list[tuple[float, str]], int, float]:
        clock = FakeClock()
        events: list[tuple[float, str]] = []
        source = SequenceSource([ok()])
        fetcher = Fetcher(settings(), clock=clock, page_source=source)
        await fetcher._request_lock.acquire()
        try:
            with pytest.raises(FetchTimeoutError, match="wait for another Statsguru request"):
                await fetcher.fetch(
                    URL, freshness=Freshness.RECENT, budget=25, progress=capture(events)
                )
        finally:
            fetcher._request_lock.release()
        return events, len(source.requests), clock.monotonic()

    events, request_count, elapsed = asyncio.run(scenario())

    assert events == [
        (0.0, "waiting for another Statsguru request"),
        (10.0, "waiting for another Statsguru request"),
        (10.0, "waiting for another Statsguru request"),
    ]
    assert request_count == 0
    assert elapsed == pytest.approx(25.0)


def test_request_lock_progress_is_not_reported_when_lock_is_free() -> None:
    clock = FakeClock()
    events: list[tuple[float, str]] = []
    source = SequenceSource([ok()])
    fetcher = Fetcher(settings(), clock=clock, page_source=source)

    assert (
        run(fetcher.fetch(URL, freshness=Freshness.RECENT, budget=10, progress=capture(events)))
        == STATS_PAGE
    )

    assert events == []


def test_raising_progress_callback_does_not_break_fetch_or_lock() -> None:
    clock = FakeClock()
    source = SequenceSource([ok(), ok(OTHER_URL)])
    fetcher = Fetcher(settings(min_interval=timedelta(seconds=2)), clock=clock, page_source=source)

    def progress(seconds: float, reason: str) -> None:
        raise RuntimeError(f"client disconnected during {reason} after {seconds}")

    assert run(fetcher.fetch(URL, freshness=Freshness.RECENT, budget=10)) == STATS_PAGE
    assert (
        run(fetcher.fetch(OTHER_URL, freshness=Freshness.RECENT, budget=10, progress=progress))
        == STATS_PAGE
    )

    assert [request[0] for request in source.requests] == [URL, OTHER_URL]


def test_cancelled_async_progress_callback_releases_lock_for_later_fetch() -> None:
    async def scenario() -> tuple[list[str], bool]:
        clock = FakeClock()
        source = SequenceSource([ok(), ok(OTHER_URL)])
        fetcher = Fetcher(
            settings(min_interval=timedelta(seconds=2)), clock=clock, page_source=source
        )
        assert await fetcher.fetch(URL, freshness=Freshness.RECENT, budget=10) == STATS_PAGE
        progress_entered = asyncio.Event()

        async def progress(seconds: float, reason: str) -> None:
            progress_entered.set()
            await asyncio.sleep(3600)

        task = asyncio.create_task(
            fetcher.fetch(OTHER_URL, freshness=Freshness.RECENT, budget=10, progress=progress)
        )
        await progress_entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        source.responses.append(ok(THIRD_URL))
        result = await fetcher.fetch(THIRD_URL, freshness=Freshness.RECENT, budget=10)
        return [request[0] for request in source.requests], result == STATS_PAGE

    urls, later_succeeded = asyncio.run(asyncio.wait_for(scenario(), timeout=1))

    assert urls == [URL, THIRD_URL]
    assert later_succeeded


def test_cancelled_spacing_sleep_releases_lock_for_later_fetch() -> None:
    async def scenario() -> tuple[list[str], bool]:
        clock = FakeClock()
        source = SequenceSource([ok(), ok(OTHER_URL)])
        fetcher = Fetcher(
            settings(min_interval=timedelta(seconds=100)), clock=clock, page_source=source
        )
        assert await fetcher.fetch(URL, freshness=Freshness.RECENT, budget=10) == STATS_PAGE
        sleep_entered = asyncio.Event()
        original_sleep = clock.sleep

        async def blocking_sleep(seconds: float) -> None:
            sleep_entered.set()
            await asyncio.sleep(3600)

        clock.sleep = blocking_sleep  # type: ignore[method-assign]
        task = asyncio.create_task(fetcher.fetch(OTHER_URL, freshness=Freshness.RECENT, budget=200))
        await sleep_entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        clock.sleep = original_sleep  # type: ignore[method-assign]
        clock.monotonic_time = 100
        source.responses.append(ok(THIRD_URL))
        result = await fetcher.fetch(THIRD_URL, freshness=Freshness.RECENT, budget=10)
        return [request[0] for request in source.requests], result == STATS_PAGE

    urls, later_succeeded = asyncio.run(asyncio.wait_for(scenario(), timeout=1))

    assert urls == [URL, THIRD_URL]
    assert later_succeeded


def test_cancelled_request_counts_for_spacing_and_releases_lock() -> None:
    class CancellableSource(SequenceSource):
        def __init__(self, clock: FakeClock) -> None:
            super().__init__([ok(), ok(OTHER_URL)])
            self.clock = clock
            self.entered = asyncio.Event()
            self.request_times: list[float] = []

        async def get(self, url: str, headers: Mapping[str, str]) -> FetchResponse:
            self.requests.append((url, headers))
            self.request_times.append(self.clock.monotonic())
            if len(self.requests) == 1:
                self.entered.set()
                await asyncio.sleep(3600)
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

    async def scenario() -> tuple[list[float], list[str]]:
        clock = FakeClock()
        source = CancellableSource(clock)
        fetcher = Fetcher(
            settings(min_interval=timedelta(seconds=15)), clock=clock, page_source=source
        )
        task = asyncio.create_task(fetcher.fetch(URL, freshness=Freshness.RECENT, budget=200))
        await source.entered.wait()
        clock.monotonic_time = 2
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await fetcher.fetch(OTHER_URL, freshness=Freshness.RECENT, budget=200) == STATS_PAGE
        return source.request_times, [request[0] for request in source.requests]

    request_times, urls = asyncio.run(asyncio.wait_for(scenario(), timeout=1))

    assert request_times == [0.0, 15.0]
    assert urls == [URL, OTHER_URL]


def test_not_before_set_while_queued_is_rechecked_inside_lock() -> None:
    class BlockingSource(SequenceSource):
        def __init__(self, clock: FakeClock) -> None:
            super().__init__([response(429, headers={"Retry-After": "100"}), ok(OTHER_URL)])
            self.clock = clock
            self.entered = asyncio.Event()
            self.release = asyncio.Event()
            self.request_times: list[float] = []

        async def get(self, url: str, headers: Mapping[str, str]) -> FetchResponse:
            self.request_times.append(self.clock.monotonic())
            if not self.entered.is_set():
                self.entered.set()
                await self.release.wait()
            return await super().get(url, headers)

    async def scenario() -> tuple[list[object], list[float], list[str]]:
        clock = FakeClock()
        source = BlockingSource(clock)
        fetcher = Fetcher(settings(max_retries=0), clock=clock, page_source=source)
        first = asyncio.create_task(fetcher.fetch(URL, freshness=Freshness.RECENT, budget=200))
        await source.entered.wait()
        second = asyncio.create_task(
            fetcher.fetch(OTHER_URL, freshness=Freshness.RECENT, budget=200)
        )
        await asyncio.sleep(0)
        source.release.set()
        results = await asyncio.gather(first, second, return_exceptions=True)
        return list(results), source.request_times, [request[0] for request in source.requests]

    results, request_times, urls = asyncio.run(scenario())

    assert type(results[0]) is FetchTimeoutError
    assert results[1] == STATS_PAGE
    assert request_times[0] == 0.0
    assert request_times[1] >= 100.0
    assert urls == [URL, OTHER_URL]


def test_concurrent_fetch_does_not_request_during_pause() -> None:
    async def scenario() -> tuple[list[object], list[str]]:
        clock = FakeClock()
        source = YieldingSequenceSource([response(403), ok(OTHER_URL)], clock)
        fetcher = Fetcher(settings(), clock=clock, page_source=source)
        results = await asyncio.gather(
            fetcher.fetch(URL, freshness=Freshness.RECENT, budget=20),
            fetcher.fetch(OTHER_URL, freshness=Freshness.RECENT, budget=20),
            return_exceptions=True,
        )
        return list(results), [request[0] for request in source.requests]

    results, urls = asyncio.run(scenario())

    assert [type(result) for result in results] == [BlockedError, BlockedError]
    assert urls == [URL]


def test_successful_response_does_not_clear_pause_set_during_request() -> None:
    class PausingSource(SequenceSource):
        def __init__(self, fetcher: Fetcher, clock: FakeClock) -> None:
            super().__init__([ok()])
            self.fetcher = fetcher
            self.clock = clock

        async def get(self, url: str, headers: Mapping[str, str]) -> FetchResponse:
            self.fetcher._block_until = self.clock.monotonic() + 10
            return await super().get(url, headers)

    clock = FakeClock()
    fetcher = Fetcher(settings(), clock=clock, page_source=SequenceSource([]))
    source = PausingSource(fetcher, clock)
    fetcher._source = source

    assert run(fetcher.fetch(URL, freshness=Freshness.RECENT, budget=20)) == STATS_PAGE

    with pytest.raises(BlockedError, match=r"paused until 08:01 \(UTC\+00:00\)"):
        run(fetcher.fetch(OTHER_URL, freshness=Freshness.RECENT, budget=20))
    assert len(source.requests) == 1


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

    with pytest.raises(FetchTimeoutError, match=r"try again after 08:02 \(UTC\+00:00\)"):
        run(fetcher.fetch(URL, freshness=Freshness.RECENT, budget=20))

    assert len(source.requests) == 1
    with pytest.raises(FetchTimeoutError, match=r"try again after 08:02 \(UTC\+00:00\)"):
        run(fetcher.fetch(OTHER_URL, freshness=Freshness.RECENT, budget=20))
    assert len(source.requests) == 1


def test_retry_after_is_recorded_when_retries_are_disabled() -> None:
    clock = FakeClock()
    source = SequenceSource([response(429, headers={"Retry-After": "3600"})])
    fetcher = Fetcher(settings(max_retries=0), clock=clock, page_source=source)

    with pytest.raises(FetchTimeoutError, match=r"try again after 09:00 \(UTC\+00:00\)"):
        run(fetcher.fetch(URL, freshness=Freshness.RECENT, budget=4000))
    with pytest.raises(FetchTimeoutError, match=r"try again after 09:00 \(UTC\+00:00\)"):
        run(fetcher.fetch(OTHER_URL, freshness=Freshness.RECENT, budget=20))

    assert len(source.requests) == 1


def test_retry_after_is_recorded_on_last_attempt() -> None:
    clock = FakeClock()
    source = SequenceSource(
        [
            response(429, headers={"Retry-After": "1"}),
            response(429, headers={"Retry-After": "3600"}),
        ]
    )
    fetcher = Fetcher(settings(max_retries=1), clock=clock, page_source=source)

    with pytest.raises(FetchTimeoutError, match=r"try again after 09:01 \(UTC\+00:00\)"):
        run(fetcher.fetch(URL, freshness=Freshness.RECENT, budget=4000))
    with pytest.raises(FetchTimeoutError, match=r"try again after 09:01 \(UTC\+00:00\)"):
        run(fetcher.fetch(OTHER_URL, freshness=Freshness.RECENT, budget=20))

    assert len(source.requests) == 2


def test_retry_after_on_5xx_is_recorded_when_retries_are_disabled() -> None:
    clock = FakeClock()
    source = SequenceSource([response(503, headers={"Retry-After": "3600"})])
    fetcher = Fetcher(settings(max_retries=0), clock=clock, page_source=source)

    with pytest.raises(FetchTimeoutError, match=r"try again after 09:00 \(UTC\+00:00\)"):
        run(fetcher.fetch(URL, freshness=Freshness.RECENT, budget=4000))
    with pytest.raises(FetchTimeoutError, match=r"try again after 09:00 \(UTC\+00:00\)"):
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


def test_jitter_lower_bound_is_ten_percent_when_default_jitter_is_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    monkeypatch.setattr("crickey.fetcher.random.uniform", lambda low, high: low)
    source = SequenceSource([response(500), ok()])
    fetcher = Fetcher(settings(), clock=clock, page_source=source)

    run(fetcher.fetch(URL, freshness=Freshness.RECENT, budget=100))

    assert clock.sleeps[0] == 13.5


def test_retry_stops_when_next_wait_would_not_fit_budget() -> None:
    source = SequenceSource([response(500)])
    fetcher = Fetcher(settings(), clock=FakeClock(), page_source=source, jitter=lambda base: 0)

    with pytest.raises(FetchTimeoutError, match="Not enough time left to retry"):
        run(fetcher.fetch(URL, freshness=Freshness.RECENT, budget=10))

    assert len(source.requests) == 1


def test_later_call_honours_pending_not_before_time() -> None:
    clock = FakeClock()
    source = SequenceSource([response(429, headers={"Retry-After": "60"})])
    fetcher = Fetcher(settings(max_retries=0), clock=clock, page_source=source)

    with pytest.raises(FetchTimeoutError):
        run(fetcher.fetch(URL, freshness=Freshness.RECENT, budget=120))
    with pytest.raises(FetchTimeoutError, match=r"try again after 08:01 \(UTC\+00:00\)"):
        run(fetcher.fetch(OTHER_URL, freshness=Freshness.RECENT, budget=10))

    assert len(source.requests) == 1


def test_later_call_waits_for_pending_not_before_when_budget_allows() -> None:
    clock = FakeClock()
    events: list[tuple[float, str]] = []
    source = SequenceSource([response(429, headers={"Retry-After": "3"}), ok(OTHER_URL)])
    fetcher = Fetcher(settings(max_retries=0), clock=clock, page_source=source)

    with pytest.raises(FetchTimeoutError):
        run(fetcher.fetch(URL, freshness=Freshness.RECENT, budget=10))
    assert (
        run(
            fetcher.fetch(
                OTHER_URL, freshness=Freshness.RECENT, budget=10, progress=capture(events)
            )
        )
        == STATS_PAGE
    )

    assert clock.sleeps == [3.0]
    assert events == [(3.0, "waiting for Statsguru's Retry-After time")]
    assert [request[0] for request in source.requests] == [URL, OTHER_URL]


def test_unavailable_urls_are_not_requested_again() -> None:
    source = SequenceSource([response(404)])
    fetcher = Fetcher(settings(), clock=FakeClock(), page_source=source)

    with pytest.raises(UnavailableUrlError, match="unavailable"):
        run(fetcher.fetch(URL, freshness=Freshness.RECENT, budget=10))
    with pytest.raises(UnavailableUrlError, match="unavailable earlier"):
        run(fetcher.fetch(URL, freshness=Freshness.RECENT, budget=10))

    assert len(source.requests) == 1


def test_concurrent_same_url_fetches_make_one_request() -> None:
    async def scenario() -> tuple[list[str], list[str]]:
        clock = FakeClock()
        source = YieldingSequenceSource([ok(text="shared Statsguru")], clock)
        fetcher = Fetcher(settings(), clock=clock, page_source=source)

        results = await asyncio.gather(
            fetcher.fetch(URL, freshness=Freshness.RECENT, budget=20),
            fetcher.fetch(URL, freshness=Freshness.RECENT, budget=20),
        )
        return list(results), [request[0] for request in source.requests]

    results, urls = asyncio.run(scenario())

    assert results == ["shared Statsguru", "shared Statsguru"]
    assert urls == [URL]


def test_concurrent_same_unavailable_url_makes_one_request() -> None:
    async def scenario() -> tuple[list[object], list[str]]:
        clock = FakeClock()
        source = YieldingSequenceSource([response(404)], clock)
        fetcher = Fetcher(settings(), clock=clock, page_source=source)

        results = await asyncio.gather(
            fetcher.fetch(URL, freshness=Freshness.RECENT, budget=20),
            fetcher.fetch(URL, freshness=Freshness.RECENT, budget=20),
            return_exceptions=True,
        )
        return list(results), [request[0] for request in source.requests]

    results, urls = asyncio.run(scenario())

    assert [type(result) for result in results] == [UnavailableUrlError, UnavailableUrlError]
    assert urls == [URL]


def test_bad_request_url_is_remembered() -> None:
    source = SequenceSource([response(400)])
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
    with pytest.raises(BlockedError, match=r"paused until 08:01 \(UTC\+00:00\)"):
        run(fetcher.fetch(OTHER_URL, freshness=Freshness.RECENT, budget=20))

    clock.monotonic_time = 12
    with pytest.raises(BlockedError):
        run(fetcher.fetch(OTHER_URL, freshness=Freshness.RECENT, budget=20))
    assert len(source.requests) == 3
    with pytest.raises(BlockedError, match=r"paused until 08:01 \(UTC\+00:00\)"):
        run(fetcher.fetch(OTHER_URL, freshness=Freshness.RECENT, budget=20))
    assert len(source.requests) == 3

    clock.monotonic_time = 23
    with pytest.raises(BlockedError, match=r"paused until 08:01 \(UTC\+00:00\)"):
        run(fetcher.fetch(OTHER_URL, freshness=Freshness.RECENT, budget=20))
    assert len(source.requests) == 3
    clock.monotonic_time = 32
    with pytest.raises(BlockedError):
        run(fetcher.fetch(OTHER_URL, freshness=Freshness.RECENT, budget=20))
    assert len(source.requests) == 4
    assert fetcher._block_step == 1
    with pytest.raises(BlockedError, match=r"paused until 08:01 \(UTC\+00:00\)"):
        run(fetcher.fetch(OTHER_URL, freshness=Freshness.RECENT, budget=20))
    assert len(source.requests) == 4

    clock.monotonic_time = 53
    assert run(fetcher.fetch(OTHER_URL, freshness=Freshness.RECENT, budget=30)) == STATS_PAGE
    assert fetcher._block_step == 0
    assert source.requests[-1][0] == OTHER_URL


def test_challenge_detection_requires_challenge_marker_without_statsguru_marker() -> None:
    challenge = "<html><title>Access denied</title><p>enable JavaScript challenge</p></html>"
    source = SequenceSource([response(200, text=challenge)])
    fetcher = Fetcher(settings(), clock=FakeClock(), page_source=source)

    with pytest.raises(BlockedError):
        run(fetcher.fetch(URL, freshness=Freshness.RECENT, budget=10))


def test_statsguru_page_with_challenge_word_is_not_a_block() -> None:
    page = (
        "<html><title>Statsguru</title><p>challenge trophy</p>"
        "<table class='engineTable'></table></html>"
    )
    source = SequenceSource([response(200, text=page)])
    fetcher = Fetcher(settings(), clock=FakeClock(), page_source=source)

    assert run(fetcher.fetch(URL, freshness=Freshness.RECENT, budget=10)) == page


def test_real_looking_challenge_page_is_a_block() -> None:
    page = (
        "<html><title>Access denied</title><script>window._cf_challenge = true</script>"
        "<p>enable javascript</p></html>"
    )
    source = SequenceSource([response(200, text=page)])
    fetcher = Fetcher(settings(), clock=FakeClock(), page_source=source)

    with pytest.raises(BlockedError):
        run(fetcher.fetch(URL, freshness=Freshness.RECENT, budget=10))


def test_retry_spacing_waits_between_every_attempt() -> None:
    clock = FakeClock()
    source = SequenceSource([response(501), response(599), ok()])
    fetcher = Fetcher(
        settings(min_interval=timedelta(seconds=20)),
        clock=clock,
        page_source=source,
        jitter=lambda base: 0,
    )

    assert run(fetcher.fetch(URL, freshness=Freshness.RECENT, budget=100)) == STATS_PAGE

    assert clock.sleeps == [15.0, 5.0, 30.0]
    assert len(source.requests) == 3


def test_all_5xx_statuses_are_retryable() -> None:
    source = SequenceSource([response(501), ok()])
    fetcher = Fetcher(settings(), clock=FakeClock(), page_source=source, jitter=lambda base: 0)

    assert run(fetcher.fetch(URL, freshness=Freshness.RECENT, budget=100)) == STATS_PAGE

    assert len(source.requests) == 2


def test_transport_errors_are_retryable() -> None:
    source = SequenceSource([httpx.RemoteProtocolError("bad close"), ok()])
    fetcher = Fetcher(settings(), clock=FakeClock(), page_source=source, jitter=lambda base: 0)

    assert run(fetcher.fetch(URL, freshness=Freshness.RECENT, budget=100)) == STATS_PAGE

    assert len(source.requests) == 2


def test_sent_request_that_fails_transport_still_counts_for_spacing() -> None:
    clock = FakeClock()
    source = YieldingSequenceSource([httpx.RemoteProtocolError("bad close"), ok()], clock)
    fetcher = Fetcher(
        settings(min_interval=timedelta(seconds=15)),
        clock=clock,
        page_source=source,
        jitter=lambda base: -1.5,
    )

    assert run(fetcher.fetch(URL, freshness=Freshness.RECENT, budget=100)) == STATS_PAGE

    assert source.request_times == [0.0, 15.0]
    assert clock.sleeps == [13.5, 1.5]


def test_malformed_retry_after_uses_backoff_and_logs(caplog: pytest.LogCaptureFixture) -> None:
    source = SequenceSource([response(429, headers={"Retry-After": "later"}), ok()])
    fetcher = Fetcher(settings(), clock=FakeClock(), page_source=source, jitter=lambda base: 0)

    with caplog.at_level("WARNING", logger="crickey.fetcher"):
        assert run(fetcher.fetch(URL, freshness=Freshness.RECENT, budget=100)) == STATS_PAGE

    assert "Ignoring malformed Retry-After header" in caplog.text


def test_http_request_timeout_is_limited_by_remaining_budget() -> None:
    seen_timeouts: list[dict[str, float]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_timeouts.append(request.extensions["timeout"])
        return httpx.Response(200, text=STATS_PAGE, request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=300)
    fetcher = Fetcher(settings(), clock=FakeClock(), client=client)

    try:
        assert run(fetcher.fetch(URL, freshness=Freshness.RECENT, budget=60)) == STATS_PAGE
    finally:
        run(fetcher.aclose())

    assert seen_timeouts[0]["connect"] == 59.5
    assert seen_timeouts[0]["read"] == 59.5


def test_request_is_not_sent_when_remaining_budget_is_below_minimum() -> None:
    clock = FakeClock()
    source = SequenceSource([ok(), ok(OTHER_URL)])
    fetcher = Fetcher(settings(min_interval=timedelta(seconds=2)), clock=clock, page_source=source)

    assert run(fetcher.fetch(URL, freshness=Freshness.RECENT, budget=10)) == STATS_PAGE
    with pytest.raises(FetchTimeoutError, match="Not enough time left to request"):
        run(fetcher.fetch(OTHER_URL, freshness=Freshness.RECENT, budget=2))

    assert [request[0] for request in source.requests] == [URL]


def test_request_refused_by_budget_does_not_update_spacing_timestamp() -> None:
    clock = FakeClock()
    source = YieldingSequenceSource([ok(), ok(THIRD_URL)], clock)
    fetcher = Fetcher(settings(min_interval=timedelta(seconds=2)), clock=clock, page_source=source)

    assert run(fetcher.fetch(URL, freshness=Freshness.RECENT, budget=10)) == STATS_PAGE
    with pytest.raises(FetchTimeoutError, match="Not enough time left to request"):
        run(fetcher.fetch(OTHER_URL, freshness=Freshness.RECENT, budget=2))
    assert run(fetcher.fetch(THIRD_URL, freshness=Freshness.RECENT, budget=10)) == STATS_PAGE

    assert source.request_times == [0.0, 2.0]
    assert clock.sleeps == [2.0]


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


def test_settled_pages_do_not_expire() -> None:
    clock = FakeClock()
    source = SequenceSource([ok(text="settled Statsguru")])
    fetcher = Fetcher(settings(), clock=clock, page_source=source)

    assert run(fetcher.fetch(URL, freshness=Freshness.SETTLED, budget=10)) == "settled Statsguru"
    clock.monotonic_time = 1_000_000
    assert run(fetcher.fetch(URL, freshness=Freshness.SETTLED, budget=10)) == "settled Statsguru"

    assert len(source.requests) == 1


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


def test_cache_eviction_is_lru_and_decrements_bytes() -> None:
    clock = FakeClock()
    third_url = f"{URL};page=3"
    source = SequenceSource(
        [
            ok(URL, text="A" * 200 + " Statsguru"),
            ok(OTHER_URL, text="B" * 200 + " Statsguru"),
            ok(third_url, text="C" * 200 + " Statsguru"),
            ok(OTHER_URL, text="B refetched Statsguru"),
        ]
    )
    fetcher = Fetcher(settings(cache_max_mb=1), clock=clock, page_source=source)
    fetcher.cache._max_bytes = 60

    assert "A" in run(fetcher.fetch(URL, freshness=Freshness.SETTLED, budget=10))
    assert "B" in run(fetcher.fetch(OTHER_URL, freshness=Freshness.SETTLED, budget=20))
    bytes_after_two = fetcher.cache.current_bytes
    assert "A" in run(fetcher.fetch(URL, freshness=Freshness.SETTLED, budget=10))
    assert "C" in run(fetcher.fetch(third_url, freshness=Freshness.SETTLED, budget=20))
    bytes_after_eviction = fetcher.cache.current_bytes
    assert (
        run(fetcher.fetch(OTHER_URL, freshness=Freshness.SETTLED, budget=20))
        == "B refetched Statsguru"
    )

    assert bytes_after_eviction <= bytes_after_two
    assert [request[0] for request in source.requests] == [URL, OTHER_URL, third_url, OTHER_URL]


def test_page_cap_refuses_after_first_page_and_keeps_it_cached() -> None:
    first = "Statsguru engineTable Page <b>1</b> of <b>3</b>"
    source = SequenceSource([ok(text=first)])
    fetcher = Fetcher(settings(max_pages=2), clock=FakeClock(), page_source=source)

    def page_url(page: int) -> str:
        return f"{URL};page={page}"

    with pytest.raises(TooBroadError, match="needs 3 pages.*limit is 2"):
        run(fetcher.fetch_pages(URL, page_url, freshness=Freshness.RECENT, budget=10))
    assert run(fetcher.fetch(URL, freshness=Freshness.RECENT, budget=10)) == first
    assert len(source.requests) == 1


def test_page_count_accepts_one_page_and_pages_without_paging_text() -> None:
    one_page = "Statsguru engineTable Page <b>1</b> of <b>1</b>"
    no_paging = "Statsguru engineTable"
    source = SequenceSource([ok(text=one_page), ok(OTHER_URL, text=no_paging)])
    fetcher = Fetcher(settings(max_pages=2), clock=FakeClock(), page_source=source)

    assert run(
        fetcher.fetch_pages(
            URL, lambda page: f"{URL};page={page}", freshness=Freshness.RECENT, budget=10
        )
    ) == [one_page]
    assert run(
        fetcher.fetch_pages(
            OTHER_URL,
            lambda page: f"{OTHER_URL};page={page}",
            freshness=Freshness.RECENT,
            budget=20,
        )
    ) == [no_paging]


def test_retry_message_uses_local_offset_and_rounds_up() -> None:
    clock = FakeClock()
    clock.wall_time = datetime(2026, 10, 4, 4, 29, 30, tzinfo=timezone(timedelta(hours=4)))
    source = SequenceSource([response(429, headers={"Retry-After": "301"})])
    fetcher = Fetcher(settings(max_retries=0), clock=clock, page_source=source)

    with pytest.raises(FetchTimeoutError, match=r"try again after 04:35 \(UTC\+04:00\)"):
        run(fetcher.fetch(URL, freshness=Freshness.RECENT, budget=400))


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
