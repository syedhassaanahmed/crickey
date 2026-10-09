from __future__ import annotations

import asyncio
import email.utils
import html
import logging
import random
import re
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from types import TracebackType
from typing import Protocol
from urllib.parse import urljoin, urlparse

import httpx

from crickey.cache import PageCache
from crickey.clock import Clock, SystemClock
from crickey.settings import Settings

LOGGER = logging.getLogger(__name__)

STATS_HOST = "stats.cricinfo.com"
STATS_ORIGIN = f"https://{STATS_HOST}"
USER_AGENT = "curl/8.21.0"

_PAGE_COUNT_RE = re.compile(r"Page\s+\d+\s+of\s+(?P<count>\d+)", re.IGNORECASE)
_REQUEST_TIMEOUT_MARGIN = 0.5
_MIN_REQUEST_TIMEOUT = 1.0
_LOCK_WAIT_PROGRESS_INTERVAL = 10.0
_STATS_MARKERS = ("engineTable", "Statsguru", "/ci/engine/")
_CHALLENGE_MARKERS = (
    "captcha",
    "challenge",
    "cf-challenge",
    "access denied",
    "enable javascript",
)


@dataclass
class RequestTally:
    """What one tool call cost: Statsguru requests sent, and pages read from the cache."""

    requests: int = 0
    cached_pages: int = 0


_request_tally: ContextVar[RequestTally | None] = ContextVar("crickey_request_tally", default=None)


def request_tally() -> RequestTally:
    """The current tool call's tally, started by `start_request_tally`."""
    tally = _request_tally.get()
    if tally is None:
        tally = start_request_tally()
    return tally


def start_request_tally() -> RequestTally:
    """Start a fresh tally at the top of each tool call. An MCP server can run its requests in
    one shared context, so a tally can't rely on each request getting a context of its own."""
    tally = RequestTally()
    _request_tally.set(tally)
    return tally


class FetcherError(RuntimeError):
    pass


class RefusedUrlError(FetcherError):
    pass


class FetchTimeoutError(FetcherError):
    pass


class UnavailableUrlError(FetcherError):
    pass


class BlockedError(FetcherError):
    pass


class TooBroadError(FetcherError):
    pass


class Freshness(StrEnum):
    SETTLED = "settled"
    RECENT = "recent"
    LOOKUP = "lookup"


Progress = Callable[[float, str], None | Awaitable[None]]
Jitter = Callable[[float], float]


@dataclass(frozen=True)
class FetchResponse:
    url: str
    status_code: int
    headers: Mapping[str, str]
    text: str


class PageSource(Protocol):
    async def get(self, url: str, headers: Mapping[str, str]) -> FetchResponse: ...


class MemoryPageSource:
    def __init__(self, pages: Mapping[str, FetchResponse | str]) -> None:
        self.pages = dict(pages)
        self.requests: list[str] = []

    async def get(self, url: str, headers: Mapping[str, str]) -> FetchResponse:
        self.requests.append(url)
        page = self.pages[url]
        if isinstance(page, FetchResponse):
            return page
        return FetchResponse(url=url, status_code=200, headers={}, text=page)


class _HttpPageSource:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def get(self, url: str, headers: Mapping[str, str]) -> FetchResponse:
        return await self.get_with_timeout(url, headers, timeout=None)

    async def get_with_timeout(
        self, url: str, headers: Mapping[str, str], *, timeout: float | None
    ) -> FetchResponse:
        response = await self._client.get(
            url, headers=headers, follow_redirects=False, timeout=timeout
        )
        return FetchResponse(
            url=str(response.url),
            status_code=response.status_code,
            headers=response.headers,
            text=response.text,
        )


def freshness_from_end_date(end_date: date | None, today: date | None = None) -> Freshness:
    today = date.today() if today is None else today
    if end_date is not None and today - end_date > timedelta(days=7):
        return Freshness.SETTLED
    return Freshness.RECENT


class Fetcher:
    def __init__(
        self,
        settings: Settings,
        *,
        clock: Clock | None = None,
        page_source: PageSource | None = None,
        client: httpx.AsyncClient | None = None,
        jitter: Jitter | None = None,
    ) -> None:
        self.settings = settings
        self.clock = SystemClock() if clock is None else clock
        self._owned_client = client is None and page_source is None
        client = httpx.AsyncClient(timeout=300) if client is None else client
        self._source = _HttpPageSource(client) if page_source is None else page_source
        self._client = client
        self._jitter = (
            (lambda base: random.uniform(-base * 0.1, base * 0.1)) if jitter is None else jitter
        )
        self._cache = PageCache(settings.cache_max_mb, timer=self.clock.monotonic)
        self._last_request_at: float | None = None
        self._not_before: float | None = None
        self._unavailable: set[str] = set()
        self._block_until: float | None = None
        self._block_step = 0
        self._request_lock = asyncio.Lock()

    @property
    def cache(self) -> PageCache:
        return self._cache

    async def aclose(self) -> None:
        if self._owned_client:
            await self._client.aclose()

    def call(self, *, budget: float | timedelta, progress: Progress | None = None) -> _FetchCall:
        seconds = budget.total_seconds() if isinstance(budget, timedelta) else float(budget)
        return _FetchCall(self, seconds, progress)

    async def fetch(
        self,
        url: str,
        *,
        freshness: Freshness,
        budget: float | timedelta,
        progress: Progress | None = None,
        force_refetch: bool = False,
    ) -> str:
        async with self.call(budget=budget, progress=progress) as call:
            return await call.fetch(url, freshness=freshness, force_refetch=force_refetch)

    async def fetch_pages(
        self,
        first_url: str,
        page_url: Callable[[int], str],
        *,
        freshness: Freshness,
        budget: float | timedelta,
        progress: Progress | None = None,
    ) -> list[str]:
        async with self.call(budget=budget, progress=progress) as call:
            return await call.fetch_pages(first_url, page_url, freshness=freshness)


class _FetchCall(AbstractAsyncContextManager["_FetchCall"]):
    def __init__(self, fetcher: Fetcher, budget: float, progress: Progress | None) -> None:
        self._fetcher = fetcher
        self._deadline = fetcher.clock.monotonic() + budget
        self._progress = progress

    async def __aenter__(self) -> _FetchCall:
        # Started here, in the tool's own task, so pages fetched by child tasks add to it.
        request_tally()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        return None

    async def fetch(
        self,
        url: str,
        *,
        freshness: Freshness,
        force_refetch: bool = False,
    ) -> str:
        _validate_url(url)
        now = self._fetcher.clock.monotonic()
        if not force_refetch:
            cached = self._fetcher.cache.get(url, now)
            if cached is not None:
                request_tally().cached_pages += 1
                return cached.text
        if url in self._fetcher._unavailable:
            raise UnavailableUrlError("This Statsguru page was unavailable earlier in this run.")
        return await self._fetch_text_with_retries(
            url, freshness=freshness, force_refetch=force_refetch
        )

    async def fetch_pages(
        self,
        first_url: str,
        page_url: Callable[[int], str],
        *,
        freshness: Freshness,
    ) -> list[str]:
        first = await self.fetch(first_url, freshness=freshness)
        page_count = _page_count(first)
        if page_count > self._fetcher.settings.max_pages:
            raise TooBroadError(
                f"That query is too broad: it needs {page_count} pages, "
                f"but the limit is {self._fetcher.settings.max_pages}."
            )
        pages = [first]
        for page in range(2, page_count + 1):
            pages.append(await self.fetch(page_url(page), freshness=freshness))
        return pages

    async def _fetch_text_with_retries(
        self, url: str, *, freshness: Freshness, force_refetch: bool
    ) -> str:
        attempts = self._fetcher.settings.max_retries + 1
        current_url = url
        attempt = 0
        while attempt < attempts:
            tested_after_pause_before_lock = await self._check_pause()
            await self._honour_not_before()
            await self._acquire_request_lock()
            transport_error: httpx.TransportError | None = None
            try:
                if not force_refetch:
                    cached = self._fetcher.cache.get(url, self._fetcher.clock.monotonic())
                    if cached is not None:
                        request_tally().cached_pages += 1
                        return cached.text
                if url in self._fetcher._unavailable:
                    raise UnavailableUrlError(
                        "This Statsguru page was unavailable earlier in this run."
                    )
                tested_after_pause = tested_after_pause_before_lock or await self._check_pause()
                if self._not_before_wait() is not None:
                    continue
                try:
                    response = await self._request_following_safe_redirects(current_url)
                except httpx.TransportError as error:
                    transport_error = error
                else:
                    if response.status_code in {400, 404}:
                        self._fetcher._unavailable.add(url)
                        raise UnavailableUrlError("This Statsguru page is unavailable.")
                    if response.status_code == 403 or _looks_like_challenge(response.text):
                        self._start_block_pause()
                        raise BlockedError(_blocked_message())
                    if response.status_code < 400:
                        expires_at = self._expires_at(freshness)
                        self._fetcher.cache.put(response.url, response.text, expires_at)
                        if response.url != url:
                            self._fetcher.cache.put(url, response.text, expires_at)
                        if tested_after_pause:
                            self._fetcher._block_step = 0
                        return response.text
            finally:
                if self._fetcher._request_lock.locked():
                    self._fetcher._request_lock.release()
            if transport_error is not None:
                if attempt + 1 >= attempts:
                    raise FetchTimeoutError(
                        "Statsguru did not respond in time."
                    ) from transport_error
                await self._retry_wait(attempt, None, has_next=True)
                attempt += 1
                continue
            if _is_retryable_status(response.status_code):
                await self._retry_wait(attempt, response, has_next=attempt + 1 < attempts)
                attempt += 1
                continue
            raise FetcherError(f"Statsguru returned HTTP {response.status_code}.")
        raise AssertionError("retry loop should have returned or raised")

    async def _request_following_safe_redirects(self, url: str) -> FetchResponse:
        current_url = url
        for _ in range(5):
            await self._space_request()
            response = await self._get(current_url)
            if response.status_code not in {301, 302, 303, 307, 308}:
                return response
            location = response.headers.get("location") or response.headers.get("Location")
            if location is None:
                return response
            current_url = urljoin(current_url, location)
            _validate_url(current_url)
        raise RefusedUrlError("Statsguru redirected too many times.")

    async def _get(self, url: str) -> FetchResponse:
        remaining = self._deadline - self._fetcher.clock.monotonic()
        timeout = remaining - _REQUEST_TIMEOUT_MARGIN
        if timeout < _MIN_REQUEST_TIMEOUT:
            raise FetchTimeoutError("Not enough time left to request Statsguru.")
        self._fetcher._last_request_at = self._fetcher.clock.monotonic()
        request_tally().requests += 1
        if isinstance(self._fetcher._source, _HttpPageSource):
            return await self._fetcher._source.get_with_timeout(
                url, {"User-Agent": USER_AGENT}, timeout=timeout
            )
        return await self._fetcher._source.get(url, {"User-Agent": USER_AGENT})

    async def _acquire_request_lock(self) -> None:
        remaining = self._deadline - self._fetcher.clock.monotonic()
        if remaining <= 0:
            raise FetchTimeoutError("Not enough time left to wait for another Statsguru request.")
        if self._fetcher._request_lock.locked():
            await self._report_wait(0.0, "waiting for another Statsguru request")
            next_progress = self._fetcher.clock.monotonic() + _LOCK_WAIT_PROGRESS_INTERVAL
            while self._fetcher._request_lock.locked():
                now = self._fetcher.clock.monotonic()
                remaining = self._deadline - now
                if remaining <= 0:
                    raise FetchTimeoutError(
                        "Not enough time left to wait for another Statsguru request."
                    )
                sleep_for = min(0.1, remaining, max(0.0, next_progress - now))
                await self._fetcher.clock.sleep(sleep_for)
                if (
                    self._fetcher._request_lock.locked()
                    and self._fetcher.clock.monotonic() >= next_progress
                ):
                    await self._report_wait(
                        _LOCK_WAIT_PROGRESS_INTERVAL, "waiting for another Statsguru request"
                    )
                    next_progress += _LOCK_WAIT_PROGRESS_INTERVAL
        if self._fetcher.clock.monotonic() >= self._deadline:
            raise FetchTimeoutError("Not enough time left to wait for another Statsguru request.")
        await self._fetcher._request_lock.acquire()

    async def _honour_not_before(self) -> None:
        wait = self._not_before_wait()
        if wait is None:
            return
        if not self._fits(wait):
            raise FetchTimeoutError(
                f"Please try again after {self._hhmm(self._fetcher._not_before)}."
            )
        await self._wait(wait, "waiting for Statsguru's Retry-After time")
        if self._not_before_wait() is not None and self._fetcher._not_before is not None:
            if self._fetcher._not_before <= self._fetcher.clock.monotonic():
                self._fetcher._not_before = None

    def _not_before_wait(self) -> float | None:
        not_before = self._fetcher._not_before
        if not_before is None:
            return None
        wait = not_before - self._fetcher.clock.monotonic()
        if wait <= 0:
            self._fetcher._not_before = None
            return None
        return wait

    async def _space_request(self) -> None:
        last = self._fetcher._last_request_at
        if last is None:
            return
        wait = (
            last
            + self._fetcher.settings.min_interval.total_seconds()
            - self._fetcher.clock.monotonic()
        )
        if wait > 0:
            if not self._fits(wait):
                raise FetchTimeoutError("Not enough time left to wait before the next request.")
            await self._wait(wait, "spacing requests politely")

    async def _retry_wait(
        self, attempt: int, response: FetchResponse | None, *, has_next: bool
    ) -> None:
        retry_after = (
            _retry_after_seconds(response, self._fetcher.clock.now()) if response else None
        )
        if retry_after is not None:
            not_before = self._fetcher.clock.monotonic() + retry_after
            current = self._fetcher._not_before
            if current is None or not_before > current:
                self._fetcher._not_before = not_before
            recorded_not_before = self._fetcher._not_before
            if not self._fits(retry_after):
                raise FetchTimeoutError(
                    f"Please try again after {self._hhmm(recorded_not_before)}."
                )
            if not has_next:
                raise FetchTimeoutError(
                    f"Please try again after {self._hhmm(recorded_not_before)}."
                )
            await self._wait(retry_after, "waiting for Statsguru's Retry-After time")
            if self._fetcher._not_before == not_before:
                self._fetcher._not_before = None
            return
        if not has_next:
            raise FetcherError(f"Statsguru returned HTTP {response.status_code}.")
        base = 15.0 * (2**attempt)
        wait = max(0.0, base + self._fetcher._jitter(base))
        if not self._fits(wait):
            raise FetchTimeoutError("Not enough time left to retry Statsguru.")
        await self._wait(wait, "waiting before retrying Statsguru")

    async def _check_pause(self) -> bool:
        block_until = self._fetcher._block_until
        if block_until is None:
            return False
        wait = block_until - self._fetcher.clock.monotonic()
        if wait > 0:
            raise BlockedError(f"Statsguru access is paused until {self._hhmm(block_until)}.")
        self._fetcher._block_until = None
        return True

    def _start_block_pause(self) -> None:
        pauses = self._fetcher.settings.block_pauses
        pause = pauses[min(self._fetcher._block_step, len(pauses) - 1)]
        self._fetcher._block_step = min(self._fetcher._block_step + 1, len(pauses) - 1)
        self._fetcher._block_until = self._fetcher.clock.monotonic() + pause.total_seconds()
        LOGGER.warning(
            "Statsguru access blocked; pausing until %s.", self._hhmm(self._fetcher._block_until)
        )

    def _expires_at(self, freshness: Freshness) -> float | None:
        if freshness in {Freshness.SETTLED, Freshness.LOOKUP}:
            return None
        return self._fetcher.clock.monotonic() + self._fetcher.settings.recent_ttl.total_seconds()

    async def _wait(self, seconds: float, reason: str) -> None:
        await self._report_wait(seconds, reason)
        await self._fetcher.clock.sleep(seconds)

    async def _report_wait(self, seconds: float, reason: str) -> None:
        if self._progress is not None:
            try:
                result = self._progress(seconds, reason)
                if result is not None:
                    await result
            except Exception:
                LOGGER.warning("Progress callback failed while %s.", reason, exc_info=True)

    def _fits(self, seconds: float) -> bool:
        return self._fetcher.clock.monotonic() + seconds <= self._deadline

    def _hhmm(self, monotonic_time: float) -> str:
        delta = monotonic_time - self._fetcher.clock.monotonic()
        wall = self._fetcher.clock.now() + timedelta(seconds=max(0, delta))
        if wall.second or wall.microsecond:
            wall = (wall + timedelta(minutes=1)).replace(second=0, microsecond=0)
        offset = wall.utcoffset() or timedelta(0)
        sign = "+" if offset >= timedelta(0) else "-"
        offset = abs(offset)
        hours, remainder = divmod(int(offset.total_seconds()), 3600)
        minutes = remainder // 60
        return f"{wall:%H:%M} (UTC{sign}{hours:02d}:{minutes:02d})"


def _validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != STATS_HOST:
        raise RefusedUrlError("crickey only fetches https://stats.cricinfo.com pages.")


def _retry_after_seconds(response: FetchResponse | None, now: datetime) -> float | None:
    if response is None:
        return None
    raw = response.headers.get("retry-after") or response.headers.get("Retry-After")
    if raw is None:
        return None
    if raw.isdecimal():
        return float(raw)
    try:
        parsed = email.utils.parsedate_to_datetime(raw)
    except TypeError, ValueError:
        LOGGER.warning("Ignoring malformed Retry-After header: %r.", raw)
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return max(0.0, (parsed - now).total_seconds())


def _looks_like_challenge(text: str) -> bool:
    lower = text.lower()
    has_stats = any(marker.lower() in lower for marker in _STATS_MARKERS)
    has_challenge = any(marker in lower for marker in _CHALLENGE_MARKERS)
    return has_challenge and not has_stats


def _is_retryable_status(status_code: int) -> bool:
    return status_code == 429 or status_code >= 500


def _blocked_message() -> str:
    return "Statsguru blocked this request; crickey will pause before trying again."


def _page_count(text: str) -> int:
    plain_text = re.sub(r"<[^>]+>", " ", html.unescape(text))
    match = _PAGE_COUNT_RE.search(plain_text)
    if match is None:
        return 1
    return int(match.group("count"))
