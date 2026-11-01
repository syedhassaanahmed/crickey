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
from helpers import make_settings as settings
from mcp import Client
from starlette.testclient import TestClient

from crickey.fetcher import Fetcher, MemoryPageSource
from crickey.server import create_server
from crickey.settings import Settings
from crickey.transport import (
    CONTAINER_HOST,
    GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS,
    NATIVE_HOST,
    TransportError,
    _bind_socket,
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


TOKEN = "0123456789abcdef" * 4


@contextmanager
def transport_test_client(settings_: Settings | None = None) -> Iterator[TestClient]:
    settings_ = settings_ or settings()
    app = streamable_http_app(create_server(settings_), settings_)
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize("in_container", [False, True])
def test_host_origin_allowlist_for_native_and_container_modes(in_container: bool) -> None:
    allowed_pairs = [
        ("127.0.0.1:8765", "http://127.0.0.1:8765"),
        ("localhost:8765", "http://localhost:8765"),
        ("[::1]:8765", "http://[::1]:8765"),
    ]
    with transport_test_client(settings(in_container=in_container)) as client:
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


def test_url_adds_its_host_and_origin_to_localhost() -> None:
    hosted = settings(urls=("https://crickey.example.com",), token=TOKEN)

    def post(host: str, origin: str | None) -> int:
        headers = {**modern_headers(host=host, origin=origin), "authorization": f"Bearer {TOKEN}"}
        return client.post("/mcp", json=discover_body(), headers=headers).status_code

    with transport_test_client(hosted) as client:
        assert post("crickey.example.com", None) == 200
        assert post("crickey.example.com", "https://crickey.example.com") == 200
        assert post("localhost:8765", "http://localhost:8765") == 200
        assert post("other.example.com", None) == 421
        assert post("crickey.example.com", "https://other.example.com") == 403


def test_token_guards_every_request_but_health() -> None:
    def post(authorization: str | None):
        headers = modern_headers(host="127.0.0.1:8765", origin=None)
        if authorization is not None:
            headers["authorization"] = authorization
        return client.post("/mcp", json=discover_body(), headers=headers)

    with transport_test_client(settings(token=TOKEN)) as client:
        missing = post(None)
        assert missing.status_code == 401
        assert missing.headers["www-authenticate"] == "Bearer"
        assert post("Bearer wrong").status_code == 401
        assert post(f"Bearer {TOKEN}x").status_code == 401
        assert post(f"Basic {TOKEN}").status_code == 401
        assert post(f"bearer {TOKEN}").status_code == 200
        assert client.get("/health").status_code == 200


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"host": "0.0.0.0"}, "The bind address 0.0.0.0"),
        (
            {"in_container": True, "urls": ("https://crickey.example.com",)},
            "The URL https://crickey.example.com",
        ),
    ],
)
def test_a_server_other_machines_can_reach_needs_a_token(
    overrides: dict[str, object], reason: str
) -> None:
    exposed = settings(port=free_port(), **overrides)

    with pytest.raises(TransportError) as exc_info:
        create_uvicorn_server(create_server(exposed), exposed)

    assert str(exc_info.value) == (
        f"{reason} lets other machines reach crickey, so set CRICKEY_TOKEN to a secret of at "
        'least 32 characters, which clients send as "Authorization: Bearer <token>".'
    )
    with_token = settings(port=free_port(), token=TOKEN, **overrides)
    _server, sock = create_uvicorn_server(create_server(with_token), with_token)
    sock.close()


def test_loopback_settings_need_no_token() -> None:
    local = settings(
        port=free_port(), host="127.0.0.1", urls=("http://localhost:9000", "http://[::1]:9000")
    )

    _server, sock = create_uvicorn_server(create_server(local), local)
    sock.close()


def test_an_address_this_machine_lacks_is_a_clear_error() -> None:
    # 192.0.2.0/24 is reserved for documentation, so no machine has it.
    with pytest.raises(TransportError, match="crickey can't listen on 192.0.2.1: .*CRICKEY_HOST"):
        _bind_socket("192.0.2.1", 0)


def test_an_ipv6_bind_address_listens_on_ipv6() -> None:
    try:
        socket.create_server(("::1", 0), family=socket.AF_INET6).close()
    except OSError:
        pytest.skip("this machine has no IPv6 loopback")

    with _bind_socket("::1", 0) as sock:
        assert sock.family == socket.AF_INET6


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
        app = streamable_http_app(create_server(settings_, fetcher=fetcher), settings_)
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


async def test_server_stops_with_listen_stream_open(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    servers: list[uvicorn.Server] = []

    class RecordingServer(uvicorn.Server):
        def __init__(self, config: uvicorn.Config) -> None:
            super().__init__(config)
            servers.append(self)

    # Only records the server that serve_http starts, so that the test can stop it.
    monkeypatch.setattr("crickey.transport.uvicorn.Server", RecordingServer)
    with anyio.fail_after(15):
        settings_ = settings(port=free_port())
        thread = threading.Thread(
            target=serve_http, args=(create_server(settings_), settings_), daemon=True
        )
        thread.start()
        while not (servers and servers[0].started):
            await asyncio.sleep(0.02)
        (server,) = servers
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


@pytest.mark.parametrize(("in_container", "host"), [(False, NATIVE_HOST), (True, CONTAINER_HOST)])
def test_http_binds_loopback_natively_and_every_address_in_a_container(
    in_container: bool, host: str
) -> None:
    settings_ = settings(port=free_port(), in_container=in_container)
    _, sock = create_uvicorn_server(create_server(settings_), settings_)

    with sock:
        assert sock.getsockname()[:2] == (host, settings_.port)


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


async def test_stdio_stdout_lines_are_jsonrpc() -> None:
    with anyio.fail_after(45):
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
                raw = await asyncio.wait_for(process.stdout.readline(), timeout=20)
                parsed = json.loads(raw)
                assert parsed["jsonrpc"] == "2.0"
                assert parsed["id"] == request["id"]
            assert parsed["result"]["tools"]
        finally:
            process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError:
                process.kill()
                await process.wait()
