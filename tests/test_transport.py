from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

import anyio
import pytest
import uvicorn
from helpers import FakeClock, free_port
from helpers import test_settings as settings
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from starlette.testclient import TestClient

from crickey.fetcher import Fetcher, MemoryPageSource
from crickey.server import create_server
from crickey.transport import (
    CONTAINER_HOST,
    GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS,
    NATIVE_HOST,
    bind_host,
    create_uvicorn_server,
    serve_http,
    streamable_http_app,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def player_search_url(search: str) -> str:
    return f"https://stats.cricinfo.com/ci/engine/stats/analysis.html?search={search};template=analysis"


def player_search_page(name: str, player_id: int) -> str:
    href = f"/ci/engine/player/{player_id}.html?class=3;type=allround"
    return f"""
    <table>
    <tr><td>{name}</td><td>AAA</td><td>
      <a href="{href}">Twenty20 Internationals player</a> (2020 - 2026, 7 matches)
    </td></tr>
    </table>
    """


def modern_headers(*, host: str, origin: str | None) -> dict[str, str]:
    headers = {
        "host": host,
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
        "mcp-protocol-version": "2026-07-28",
        "mcp-method": "server/discover",
    }
    if origin is not None:
        headers["origin"] = origin
    return headers


def discover_body() -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "server/discover",
        "params": {
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientCapabilities": {},
            }
        },
    }


@contextmanager
def transport_test_client(host: str) -> Iterator[TestClient]:
    app = streamable_http_app(create_server(settings()), host=host)
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize("app_host", [NATIVE_HOST, CONTAINER_HOST])
def test_host_origin_allowlist_for_native_and_container_modes(app_host: str) -> None:
    allowed_pairs = [
        ("127.0.0.1:8765", "http://127.0.0.1:8765"),
        ("localhost:8765", "http://localhost:8765"),
        ("[::1]:8765", "http://[::1]:8765"),
    ]
    with transport_test_client(app_host) as client:
        for host, origin in allowed_pairs:
            response = client.post(
                "/mcp", json=discover_body(), headers=modern_headers(host=host, origin=origin)
            )
            assert response.status_code == 200
            assert response.json()["result"]["supportedVersions"] == ["2026-07-28"]
            assert "access-control-allow-origin" not in {key.lower() for key in response.headers}

        assert (
            client.post(
                "/mcp",
                json=discover_body(),
                headers=modern_headers(host="example.test:8765", origin="http://127.0.0.1:8765"),
            ).status_code
            == 421
        )
        assert (
            client.post(
                "/mcp",
                json=discover_body(),
                headers=modern_headers(host="127.0.0.1:8765", origin="http://example.test:8765"),
            ).status_code
            == 403
        )


def test_health_returns_exact_ok() -> None:
    with transport_test_client(NATIVE_HOST) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.content == b'{"status":"ok"}'
    assert response.json() == {"status": "ok"}


def test_bind_host_native_and_container_rules() -> None:
    assert bind_host(settings()) == NATIVE_HOST
    assert bind_host(settings(in_container=True)) == CONTAINER_HOST


@contextmanager
def running_server(app, port: int) -> Iterator[uvicorn.Server]:
    config = uvicorn.Config(
        app,
        host=NATIVE_HOST,
        port=port,
        log_level="warning",
        lifespan="on",
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    wait_for_server(port)
    try:
        yield server
    finally:
        server.should_exit = True
        thread.join(timeout=8)
        assert not thread.is_alive()


def wait_for_server(port: int) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.1)
            if sock.connect_ex((NATIVE_HOST, port)) == 0:
                return
        time.sleep(0.05)
    raise RuntimeError("uvicorn test server did not start")


async def test_http_sse_carries_progress_notifications() -> None:
    with anyio.fail_after(15):
        settings_ = settings(min_interval=timedelta(seconds=2))
        clock = FakeClock()
        source = MemoryPageSource(
            {
                player_search_url("One"): player_search_page("One", 1),
                player_search_url("Two"): player_search_page("Two", 2),
            }
        )
        fetcher = Fetcher(settings_, clock=clock, page_source=source)
        app = streamable_http_app(create_server(settings_, fetcher=fetcher), host=NATIVE_HOST)
        port = free_port()
        progress: list[tuple[float, float | None, str | None]] = []

        async def on_progress(
            progress_value: float, total: float | None, message: str | None
        ) -> None:
            progress.append((progress_value, total, message))

        with running_server(app, port):
            async with Client(f"http://127.0.0.1:{port}/mcp", read_timeout_seconds=5) as client:
                await client.call_tool("find_player", {"name": "One", "format": "T20I"})
                result = await client.call_tool(
                    "find_player",
                    {"name": "Two", "format": "T20I"},
                    progress_callback=on_progress,
                )

        assert result.structured_content["status"] == "match"
        assert progress == [(2.0, None, "spacing requests politely for 2 seconds")]
        assert clock.sleeps == [2.0]


async def test_server_stops_with_listen_stream_open(capsys: pytest.CaptureFixture[str]) -> None:
    with anyio.fail_after(15):
        settings_ = settings(port=free_port())
        server, sock = create_uvicorn_server(create_server(settings_), settings_)
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        wait_for_server(settings_.port)
        try:
            async with Client(
                f"http://127.0.0.1:{settings_.port}/mcp", read_timeout_seconds=5
            ) as client:
                async with client.listen(tools_list_changed=True):
                    server.should_exit = True
                    await asyncio.to_thread(thread.join, 8)
                    assert not thread.is_alive()
        finally:
            server.should_exit = True
            thread.join(timeout=8)
            if thread.is_alive():
                pytest.fail("server did not stop with a subscriptions/listen stream open")

    error = capsys.readouterr().err
    assert "Traceback" not in error
    assert "Exception in ASGI application" not in error
    assert "Cancel 1 running task(s)" not in error


def test_serve_http_binds_once_and_hands_socket_to_uvicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, int, str, int, float]] = []

    class FakeServer:
        def __init__(self, config: uvicorn.Config) -> None:
            self.config = config

        async def shutdown(self) -> None:
            return None

        def run(self, sockets: list[socket.socket]) -> None:
            assert len(sockets) == 1
            bound_host, bound_port = sockets[0].getsockname()[:2]
            seen.append(
                (
                    self.config.host,
                    self.config.port,
                    bound_host,
                    bound_port,
                    self.config.timeout_graceful_shutdown,
                )
            )

    monkeypatch.setattr("crickey.transport.uvicorn.Server", FakeServer)
    monkeypatch.setattr(
        "crickey.transport.uvicorn.run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("uvicorn.run should not be called")
        ),
    )

    native = settings(port=free_port())
    serve_http(create_server(native), native)
    container = settings(port=free_port(), in_container=True)
    serve_http(create_server(container), container)

    assert seen == [
        (NATIVE_HOST, native.port, NATIVE_HOST, native.port, GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS),
        (
            CONTAINER_HOST,
            container.port,
            "0.0.0.0",
            container.port,
            GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS,
        ),
    ]


def test_sigint_exits_zero_without_traceback() -> None:
    port = free_port()
    script = f"""
import signal
import sys
import threading
import time
import urllib.request
from crickey.cli import main

port = {port}

def interrupt_when_ready():
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            url = f"http://127.0.0.1:{{port}}/health"
            with urllib.request.urlopen(url, timeout=0.2) as response:
                if response.read() == b'{{"status":"ok"}}':
                    signal.raise_signal(signal.SIGINT)
                    return
        except Exception:
            time.sleep(0.05)
    print("server did not become ready", file=sys.stderr)
    signal.raise_signal(signal.SIGINT)

threading.Thread(target=interrupt_when_ready, daemon=True).start()
raise SystemExit(main(["serve", "--port", str(port)]))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )

    assert completed.returncode == 0
    assert "Traceback" not in completed.stderr
    assert "KeyboardInterrupt" not in completed.stderr


async def test_stdio_subprocess_lists_tools_and_stdout_is_protocol() -> None:
    command = sys.executable
    args = ["-c", "from crickey.cli import main; raise SystemExit(main(['stdio']))"]
    params = StdioServerParameters(command=command, args=args, cwd=str(Path.cwd()))

    with anyio.fail_after(10):
        async with Client(params, read_timeout_seconds=5) as client:
            tools = (await client.list_tools()).tools

    assert {tool.name for tool in tools} == {
        "better_than_player",
        "find_player",
        "leaderboard",
        "player_record",
        "query_stats",
    }


async def test_stdio_stdout_lines_are_jsonrpc() -> None:
    with anyio.fail_after(10):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "from crickey.cli import main; raise SystemExit(main(['stdio']))",
            cwd=Path.cwd(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert process.stdin is not None
        assert process.stdout is not None
        requests = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "server/discover",
                "params": {
                    "_meta": {
                        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                        "io.modelcontextprotocol/clientCapabilities": {},
                    }
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {
                    "_meta": {
                        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                        "io.modelcontextprotocol/clientCapabilities": {},
                    }
                },
            },
        ]
        try:
            for request in requests:
                process.stdin.write(json.dumps(request).encode() + b"\n")
                await process.stdin.drain()
                raw = await asyncio.wait_for(process.stdout.readline(), timeout=5)
                parsed = json.loads(raw)
                assert parsed["jsonrpc"] == "2.0"
                assert parsed["id"] == request["id"]
        finally:
            process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError:
                process.kill()
                await process.wait()
