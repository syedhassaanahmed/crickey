from __future__ import annotations

import zlib
from collections import OrderedDict
from dataclasses import dataclass


@dataclass(frozen=True)
class CachedPage:
    url: str
    text: str


@dataclass
class _Entry:
    compressed: bytes
    expires_at: float | None
    size: int


class PageCache:
    def __init__(self, max_mb: int) -> None:
        self._max_bytes = max_mb * 1024 * 1024
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._bytes = 0

    @property
    def current_bytes(self) -> int:
        return self._bytes

    def get(self, url: str, now: float) -> CachedPage | None:
        entry = self._entries.get(url)
        if entry is None:
            return None
        if entry.expires_at is not None and entry.expires_at <= now:
            self._remove(url)
            return None
        self._entries.move_to_end(url)
        return CachedPage(url=url, text=zlib.decompress(entry.compressed).decode("utf-8"))

    def put(self, url: str, text: str, expires_at: float | None) -> None:
        compressed = zlib.compress(text.encode("utf-8"))
        entry = _Entry(compressed=compressed, expires_at=expires_at, size=len(compressed))
        if url in self._entries:
            self._remove(url)
        if entry.size > self._max_bytes:
            return
        self._entries[url] = entry
        self._bytes += entry.size
        while self._bytes > self._max_bytes and self._entries:
            self._remove(next(iter(self._entries)))

    def _remove(self, url: str) -> None:
        entry = self._entries.pop(url)
        self._bytes -= entry.size
