from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    def monotonic(self) -> float: ...

    def now(self) -> datetime: ...

    async def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def now(self) -> datetime:
        return datetime.now().astimezone()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
