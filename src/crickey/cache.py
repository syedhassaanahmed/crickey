from __future__ import annotations

import zlib
from collections.abc import Callable
from dataclasses import dataclass
from math import inf
from time import monotonic

from cachetools import TLRUCache


@dataclass(frozen=True)
class CachedPage:
    url: str
    text: str


@dataclass
class _Entry:
    compressed: bytes
    expires_at: float | None


class PageCache:
    def __init__(self, max_mb: int, timer: Callable[[], float] = monotonic) -> None:
        max_bytes = max_mb * 1024 * 1024
        self._entries: TLRUCache[str, _Entry] = TLRUCache(
            maxsize=max_bytes,
            ttu=lambda _url, entry, _now: inf if entry.expires_at is None else entry.expires_at,
            timer=timer,
            getsizeof=lambda entry: len(entry.compressed),
        )

    @property
    def current_bytes(self) -> int:
        self._entries.expire()
        return self._entries.currsize

    def get(self, url: str, now: float) -> CachedPage | None:
        try:
            entry = self._entries[url]
        except KeyError:
            return None
        if entry.expires_at is not None and entry.expires_at <= now:
            self._entries.pop(url, None)
            return None
        return CachedPage(url=url, text=zlib.decompress(entry.compressed).decode("utf-8"))

    def put(self, url: str, text: str, expires_at: float | None) -> None:
        compressed = zlib.compress(text.encode("utf-8"))
        entry = _Entry(compressed=compressed, expires_at=expires_at)
        if len(entry.compressed) > self._entries.maxsize:
            self._entries.pop(url, None)
            return
        self._entries[url] = entry
