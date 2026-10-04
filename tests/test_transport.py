from __future__ import annotations

import asyncio
import socket
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import uvicorn
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from starlette.testclient import TestClient

from crickey.fetcher import Fetcher, MemoryPageSource
from crickey.server import create_server
from crickey.settings import Settings
from crickey.transport import TransportError, bind_host, streamable_http_app

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeClock:
    def __init__(self) -> None:
        self.monotonic_time = 0.0
        self.wall_time = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.monotonic_time

    def now(self) -> datetime:
        return self.wall_time + timedelta(seconds=self.monotonic_time)

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.monotonic_time += seconds
        await asyncio.sleep(0)


def settings(**overrides: object) -> Settings:
    values = {
        "min_interval": timedelta(seconds=0),
        "max_retries": 0,
        "block_pauses": (timedelta(seconds=10),),
        "max_pages": 4,
        "cache_max_mb": 1,
        "recent_ttl": timedelta(seconds=30),
        "port": 8765,
        "in_container": False,
    }
    values.update(overrides)
    return Settings(**values)


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


def modern_headers(
    *, host: str = "127.0.0.1:8765", origin: str | None = "http://127.0.0.1:8765"
) -> dict[str, str]:
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
def transport_test_client() -> Iterator[TestClient]:
    app = streamable_http_app(create_server(settings()), host="127.0.0.1")
    with TestClient(app) as client:
        yield client


def test_wrong_host_bad_origin_allowed_pair_and_no_cors() -> None:
    with transport_test_client() as client:
        wrong_host = client.post(
            "/mcp", json=discover_body(), headers=modern_headers(host="example.test:8765")
        )
        bad_origin = client.post(
            "/mcp", json=discover_body(), headers=modern_headers(origin="http://example.test:8765")
        )
        allowed = client.post("/mcp", json=discover_body(), headers=modern_headers())

    assert wrong_host.status_code == 421
    assert bad_origin.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json()["result"]["supportedVersions"] == ["2026-07-28"]
    assert "access-control-allow-origin" not in {key.lower() for key in allowed.headers}


def test_health_returns_exact_ok() -> None:
    with transport_test_client() as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.content == b'{"status":"ok"}'
    assert response.json() == {"status": "ok"}


def test_bind_host_native_and_container_rules() -> None:
    assert bind_host(settings()) == "127.0.0.1"
    assert bind_host(settings(in_container=True)) == "0.0.0.0"
    with pytest.raises(TransportError, match="native mode must bind to 127.0.0.1"):
        bind_host(settings(), requested_host="0.0.0.0")


def free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextmanager
def running_server(app, port: int) -> Iterator[None]:
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.1)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                break
        time.sleep(0.05)
    else:
        server.should_exit = True
        thread.join(timeout=5)
        raise RuntimeError("uvicorn test server did not start")
    try:
        yield
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        assert not thread.is_alive()


async def test_http_sse_carries_progress_notifications() -> None:
    settings_ = settings(min_interval=timedelta(seconds=2))
    clock = FakeClock()
    source = MemoryPageSource(
        {
            player_search_url("One"): player_search_page("One", 1),
            player_search_url("Two"): player_search_page("Two", 2),
        }
    )
    fetcher = Fetcher(settings_, clock=clock, page_source=source)
    app = streamable_http_app(create_server(settings_, fetcher=fetcher), host="127.0.0.1")
    port = free_port()
    progress: list[tuple[float, float | None, str | None]] = []

    async def on_progress(progress_value: float, total: float | None, message: str | None) -> None:
        progress.append((progress_value, total, message))

    with running_server(app, port):
        async with Client(f"http://127.0.0.1:{port}/mcp") as client:
            await client.call_tool("find_player", {"name": "One", "format": "T20I"})
            result = await client.call_tool(
                "find_player",
                {"name": "Two", "format": "T20I"},
                progress_callback=on_progress,
            )

    assert result.structured_content["status"] == "match"
    assert progress == [(2.0, None, "spacing requests politely for 2 seconds")]
    assert clock.sleeps == [2.0]


async def test_stdio_subprocess_lists_tools_and_stdout_is_protocol() -> None:
    command = sys.executable
    args = ["-c", "from crickey.cli import main; raise SystemExit(main(['stdio']))"]
    params = StdioServerParameters(command=command, args=args, cwd=str(Path.cwd()))

    async with Client(params) as client:
        tools = (await client.list_tools()).tools

    assert [tool.name for tool in tools] == ["find_player", "query_stats"]
