from __future__ import annotations

import argparse
import atexit
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any

AUDIT_EVENTS: list[dict[str, Any]] = []
_REPORT = None


def _record(event: str, args: tuple[Any, ...]) -> None:
    if event == "open":
        if args and isinstance(args[0], int):
            return
        path = str(args[0]) if args else ""
        mode = args[1] if len(args) > 1 else None
        flags = args[2] if len(args) > 2 else None
        write = isinstance(mode, str) and any(marker in mode for marker in ("w", "a", "x", "+"))
        if isinstance(flags, int):
            write = write or bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT))
        if write:
            AUDIT_EVENTS.append({"event": event, "path": path, "mode": mode, "flags": flags})
        return
    if event == "sqlite3.connect":
        if args and str(args[0]) != ":memory:":
            AUDIT_EVENTS.append({"event": event, "database": str(args[0])})
        return
    if event in {
        "os.mkdir",
        "os.makedirs",
        "os.rename",
        "os.replace",
        "os.remove",
        "os.rmdir",
        "os.unlink",
        "shutil.copyfile",
        "shutil.copymode",
        "shutil.copystat",
        "shutil.copytree",
        "shutil.move",
        "shutil.rmtree",
    }:
        AUDIT_EVENTS.append({"event": event, "args": [str(arg) for arg in args]})


sys.addaudithook(_record)


def _write_audit_to_stderr() -> None:
    if "--audit-stderr" in sys.argv and _REPORT is not None:
        print("CRICKEY_E2E_AUDIT " + json.dumps(_REPORT()), file=sys.stderr, flush=True)


atexit.register(_write_audit_to_stderr)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


class E2EPageSource:
    def __init__(self, pages: dict[str, Any]) -> None:
        self.pages = {
            url: list(value) if isinstance(value, list) else value for url, value in pages.items()
        }
        self.requests: list[str] = []

    async def get(self, url: str, headers) -> Any:
        from crickey.fetcher import FetchResponse

        self.requests.append(url)
        value = self.pages[url]
        if isinstance(value, list):
            page = value.pop(0)
            if not value:
                self.pages[url] = page
        else:
            page = value
        if isinstance(page, FetchResponse):
            return page
        return FetchResponse(url=url, status_code=200, headers={}, text=page)


class _RuntimeState:
    def __init__(self, source, clock, fetcher, scenario: str) -> None:
        self.source = source
        self.clock = clock
        self.fetcher = fetcher
        self.scenario = scenario

    def report(self) -> dict[str, Any]:
        return {
            "events": AUDIT_EVENTS,
            "requests": self.source.requests,
            "sleeps": self.clock.sleeps,
            "scenario": self.scenario,
        }

    def clear(self) -> dict[str, Any]:
        self.source.requests.clear()
        self.clock.sleeps.clear()
        self.fetcher._last_request_at = None
        return self.report()


def _build_server(scenario: str, port: int, audit: bool):
    sys.path.insert(0, str(_repo_root() / "src"))
    from e2e_pages import pages_for
    from helpers import FakeClock
    from starlette.responses import JSONResponse

    from crickey.fetcher import Fetcher
    from crickey.server import create_server
    from crickey.settings import Settings

    clock = FakeClock()
    source = E2EPageSource(pages_for(scenario))
    settings = Settings(port=port)
    fetcher = Fetcher(settings, clock=clock, page_source=source, jitter=lambda base: 0)
    mcp = create_server(settings, fetcher=fetcher)
    state = _RuntimeState(source, clock, fetcher, scenario)

    global _REPORT
    _REPORT = state.report

    @mcp.custom_route("/__e2e_audit", methods=["GET"], include_in_schema=False)
    async def audit_report(_request):
        return JSONResponse(state.report())

    @mcp.custom_route("/__e2e_clear", methods=["POST"], include_in_schema=False)
    async def clear_report(_request):
        return JSONResponse(state.clear())

    @mcp.custom_route("/__e2e_shutdown", methods=["POST"], include_in_schema=False)
    async def shutdown(_request):
        def stop() -> None:
            time.sleep(0.1)
            signal.raise_signal(signal.SIGINT)

        threading.Thread(target=stop, daemon=True).start()
        return JSONResponse({"status": "stopping"})

    return mcp, settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("transport", choices=("http", "stdio"))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--scenario", default="happy")
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--audit-stderr", action="store_true")
    args = parser.parse_args(argv)

    mcp, settings = _build_server(args.scenario, args.port, args.audit)
    if args.transport == "http":
        from crickey.transport import serve_http

        serve_http(mcp, settings)
        return 0
    from crickey.transport import run_stdio

    run_stdio(mcp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
