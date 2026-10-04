from __future__ import annotations

import argparse
import atexit
import json
import os
import sys
from pathlib import Path
from typing import Any

AUDIT_EVENTS: list[dict[str, Any]] = []


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
    if "--audit-stderr" in sys.argv:
        print("CRICKEY_E2E_AUDIT " + json.dumps({"events": AUDIT_EVENTS}), file=sys.stderr)


atexit.register(_write_audit_to_stderr)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _build_server(scenario: str, port: int, audit: bool):
    sys.path.insert(0, str(_repo_root() / "src"))
    from e2e_pages import E2EClock, pages_for
    from starlette.responses import JSONResponse

    from crickey.fetcher import Fetcher, MemoryPageSource
    from crickey.server import create_server
    from crickey.settings import Settings

    clock = E2EClock()
    source = MemoryPageSource(pages_for(scenario))
    settings = Settings(
        min_interval=__import__("datetime").timedelta(seconds=0),
        max_retries=0,
        block_pauses=(__import__("datetime").timedelta(seconds=10),),
        max_pages=1 if scenario == "broad" else 4,
        cache_max_mb=1,
        recent_ttl=__import__("datetime").timedelta(seconds=30),
        port=port,
        in_container=False,
    )
    fetcher = Fetcher(settings, clock=clock, page_source=source)
    mcp = create_server(settings, fetcher=fetcher)

    def report() -> dict[str, Any]:
        return {
            "events": AUDIT_EVENTS,
            "requests": source.requests,
            "sleeps": clock.sleeps,
            "scenario": scenario,
        }

    if audit:

        @mcp.tool(description="Return e2e audit data.")
        async def e2e_audit_report() -> dict[str, Any]:
            return report()

    @mcp.custom_route("/__e2e_audit", methods=["GET"], include_in_schema=False)
    async def audit_report(_request):
        return JSONResponse(report())

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
