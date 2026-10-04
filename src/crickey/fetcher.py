from __future__ import annotations

import email.utils
import logging
import random
import re
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager
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
_STATS_MARKERS = ("engineTable", "Statsguru", "/ci/engine/")
_CHALLENGE_MARKERS = (
    "captcha",
    "challenge",
    "cf-challenge",
    "access denied",
    "enable javascript",
)


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
        response = await self._client.get(url, headers=headers, follow_redirects=False)
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
        self._cache = PageCache(settings.cache_max_mb)
        self._last_request_at: float | None = None
        self._not_before: float | None = None
        self._unavailable: set[str] = set()
        self._block_until: float | None = None
        self._block_step = 0

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
                return cached.text
        if url in self._fetcher._unavailable:
            raise UnavailableUrlError("This Statsguru page was unavailable earlier in this run.")
        await self._check_pause()
        response = await self._request_with_retries(url)
        if response.status_code in {400, 404}:
            self._fetcher._unavailable.add(url)
            raise UnavailableUrlError("This Statsguru page is unavailable.")
        if response.status_code == 403 or _looks_like_challenge(response.text):
            self._start_block_pause()
            raise BlockedError(_blocked_message())
        if response.status_code >= 400:
            raise FetcherError(f"Statsguru returned HTTP {response.status_code}.")
        expires_at = self._expires_at(freshness)
        self._fetcher.cache.put(response.url, response.text, expires_at)
        if response.url != url:
            self._fetcher.cache.put(url, response.text, expires_at)
        self._fetcher._block_step = 0
        self._fetcher._block_until = None
        return response.text

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

    async def _request_with_retries(self, url: str) -> FetchResponse:
        attempts = self._fetcher.settings.max_retries + 1
        current_url = url
        for attempt in range(attempts):
            await self._honour_not_before()
            try:
                response = await self._request_following_safe_redirects(current_url)
            except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as error:
                if attempt + 1 >= attempts:
                    raise FetchTimeoutError("Statsguru did not respond in time.") from error
                await self._retry_wait(attempt, None)
                continue
            if response.status_code in {429, 500, 502, 503, 504} and attempt + 1 < attempts:
                await self._retry_wait(attempt, response)
                continue
            return response
        raise AssertionError("retry loop should have returned or raised")

    async def _request_following_safe_redirects(self, url: str) -> FetchResponse:
        current_url = url
        for _ in range(5):
            await self._space_request()
            self._fetcher._last_request_at = self._fetcher.clock.monotonic()
            response = await self._fetcher._source.get(current_url, {"User-Agent": USER_AGENT})
            if response.status_code not in {301, 302, 303, 307, 308}:
                return response
            location = response.headers.get("location") or response.headers.get("Location")
            if location is None:
                return response
            current_url = urljoin(current_url, location)
            _validate_url(current_url)
        raise RefusedUrlError("Statsguru redirected too many times.")

    async def _honour_not_before(self) -> None:
        not_before = self._fetcher._not_before
        if not_before is None:
            return
        wait = not_before - self._fetcher.clock.monotonic()
        if wait <= 0:
            self._fetcher._not_before = None
            return
        if not self._fits(wait):
            raise FetchTimeoutError(f"Please try again after {self._hhmm(not_before)}.")
        await self._wait(wait, "waiting for Statsguru's Retry-After time")
        self._fetcher._not_before = None

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

    async def _retry_wait(self, attempt: int, response: FetchResponse | None) -> None:
        retry_after = (
            _retry_after_seconds(response, self._fetcher.clock.now()) if response else None
        )
        if retry_after is not None:
            self._fetcher._not_before = self._fetcher.clock.monotonic() + retry_after
            if not self._fits(retry_after):
                raise FetchTimeoutError(
                    f"Please try again after {self._hhmm(self._fetcher._not_before)}."
                )
            await self._wait(retry_after, "waiting for Statsguru's Retry-After time")
            self._fetcher._not_before = None
            return
        base = 15.0 * (2**attempt)
        wait = max(0.0, base + self._fetcher._jitter(base))
        if not self._fits(wait):
            raise FetchTimeoutError("Not enough time left to retry Statsguru.")
        await self._wait(wait, "waiting before retrying Statsguru")

    async def _check_pause(self) -> None:
        block_until = self._fetcher._block_until
        if block_until is None:
            return
        wait = block_until - self._fetcher.clock.monotonic()
        if wait > 0:
            raise BlockedError(f"Statsguru access is paused until {self._hhmm(block_until)}.")
        self._fetcher._block_until = None

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
        if self._progress is not None:
            result = self._progress(seconds, reason)
            if result is not None:
                await result
        await self._fetcher.clock.sleep(seconds)

    def _fits(self, seconds: float) -> bool:
        return self._fetcher.clock.monotonic() + seconds <= self._deadline

    def _hhmm(self, monotonic_time: float) -> str:
        delta = monotonic_time - self._fetcher.clock.monotonic()
        wall = self._fetcher.clock.now() + timedelta(seconds=max(0, delta))
        return wall.strftime("%H:%M")


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
    parsed = email.utils.parsedate_to_datetime(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return max(0.0, (parsed - now).total_seconds())


def _looks_like_challenge(text: str) -> bool:
    lower = text.lower()
    has_stats = any(marker.lower() in lower for marker in _STATS_MARKERS)
    has_challenge = any(marker in lower for marker in _CHALLENGE_MARKERS)
    return has_challenge and not has_stats


def _blocked_message() -> str:
    return "Statsguru blocked this request; crickey will pause before trying again."


def _page_count(text: str) -> int:
    match = _PAGE_COUNT_RE.search(text)
    if match is None:
        return 1
    return int(match.group("count"))
