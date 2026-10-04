from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, closing, contextmanager
from pathlib import Path
from uuid import uuid4

import anyio
import pytest
from e2e_pages import AS_OF
from mcp import Client
from mcp.client.stdio import StdioServerParameters

pytestmark = pytest.mark.anyio

TOOL_NAMES = {
    "better_than_player",
    "find_player",
    "leaderboard",
    "player_record",
    "query_stats",
}
LAUNCHER = Path(__file__).with_name("e2e_server.py")
REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def launcher_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    paths = [str(REPO / "src"), str(REPO / "tests")]
    if env.get("PYTHONPATH"):
        paths.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(paths)
    if extra:
        env.update(extra)
    return env


def _wait_for_health(port: int, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 12
    url = f"http://127.0.0.1:{port}/health"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=1)
            raise RuntimeError(
                f"e2e server exited early with {process.returncode}\n"
                f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}"
            )
        try:
            with urllib.request.urlopen(url, timeout=0.25) as response:
                if response.read() == b'{"status":"ok"}':
                    return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("e2e server did not become healthy")


@contextmanager
def http_launcher(
    scenario: str = "happy",
    *,
    audit: bool = False,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> Iterator[tuple[int, subprocess.Popen[str]]]:
    port = free_port()
    args = [
        sys.executable,
        "-B",
        str(LAUNCHER),
        "http",
        "--port",
        str(port),
        "--scenario",
        scenario,
    ]
    if audit:
        args.append("--audit")
    process = subprocess.Popen(
        args,
        cwd=cwd or REPO,
        env=env or launcher_env(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_for_health(port, process)
        yield port, process
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=8)


async def _http_audit(port: int) -> dict[str, object]:
    def fetch() -> dict[str, object]:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/__e2e_audit", timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))

    return await asyncio.to_thread(fetch)


@asynccontextmanager
async def e2e_client(
    transport: str,
    scenario: str = "happy",
    *,
    audit: bool = False,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> AsyncIterator[tuple[Client, int | None]]:
    if transport == "http":
        with http_launcher(scenario, audit=audit, cwd=cwd, env=env) as (port, _process):
            async with Client(f"http://127.0.0.1:{port}/mcp", read_timeout_seconds=6) as client:
                yield client, port
        return
    args = [str(LAUNCHER), "stdio", "--scenario", scenario]
    if audit:
        args.append("--audit")
    params = StdioServerParameters(
        command=sys.executable,
        args=["-B", *args],
        cwd=str(cwd or REPO),
        env=env or launcher_env(),
    )
    async with Client(params, read_timeout_seconds=6) as client:
        yield client, None


async def call_happy_tools(client: Client, *, expect_exact_tools: bool = True) -> dict[str, object]:
    tools = (await client.list_tools()).tools
    names = {tool.name for tool in tools}
    if expect_exact_tools:
        assert names == TOOL_NAMES
    else:
        assert TOOL_NAMES <= names

    find = await client.call_tool("find_player", {"name": "Babar Azam", "format": "ODI"})
    leaderboard = await client.call_tool(
        "leaderboard",
        {"format": "ODI", "metric": "innings_per_hundred", "minimum": 10, "top_n": 2},
    )
    comparison = await client.call_tool(
        "better_than_player",
        {"player_name": "Babar Azam", "format": "T20I", "metrics": ["average", "strike_rate"]},
    )
    record = await client.call_tool(
        "player_record", {"player_name": "Babar Azam", "format": "ODI", "trophy": "World Cup"}
    )
    query_stats = await client.call_tool(
        "query_stats",
        {
            "query": {
                "class": 2,
                "type": "bowling",
                "qualifications": [{"field": "wickets", "minimum": 1}],
                "orderby": "wickets",
                "orderbyad": "reverse",
            },
            "limit": 1,
        },
    )

    assert find.structured_content["status"] == "match"
    assert leaderboard.structured_content["status"] == "ok"
    assert comparison.structured_content["status"] == "ok"
    assert record.structured_content["status"] == "ok"
    assert query_stats.structured_content["total"] == 1
    assert query_stats.structured_content["rows"][0]["Player"] == "Rashid Khan (AFG)"
    assert "stats.cricinfo.com" in query_stats.structured_content["link"]

    for result in (leaderboard, comparison, record):
        answer = result.structured_content["answer_markdown"]
        assert "https://stats.cricinfo.com/" in answer
        assert " profile](https://stats.cricinfo.com/ci/content/player/" in answer
        assert f"As of: {AS_OF.day} Oct {AS_OF.year}" in answer
        assert "Freshness: newest match Statsguru included is Example XI v Sample XI" in answer

    return {
        "find_player": find,
        "leaderboard": leaderboard,
        "better_than_player": comparison,
        "player_record": record,
        "query_stats": query_stats,
    }


@pytest.mark.parametrize("transport", ["http", "stdio"])
async def test_mcp_clients_call_all_five_tools_over_http_and_stdio(transport: str) -> None:
    with anyio.fail_after(20):
        async with e2e_client(transport) as (client, _port):
            results = await call_happy_tools(client)

    assert results["leaderboard"].structured_content["rows"][0]["player"] == "Alpha"
    assert (
        results["better_than_player"].structured_content["beaters"][0]["player"] == "Karanbir Singh"
    )
    assert results["player_record"].structured_content["row"]["100"] == 1


@pytest.mark.parametrize("transport", ["http", "stdio"])
async def test_e2e_clarification_results_over_http_and_stdio(transport: str) -> None:
    with anyio.fail_after(15):
        async with e2e_client(transport) as (client, _port):
            player = await client.call_tool(
                "better_than_player",
                {"player_name": "Babar", "format": "T20I", "metrics": ["runs"]},
            )

    assert player.structured_content["status"] == "needs_clarification"
    assert {candidate["name"] for candidate in player.structured_content["candidates"]} == {
        "Babar Azam",
        "Babar Hayat",
    }
    assert "Babar Hayat" in player.content[0].text


async def test_e2e_filter_clarification_over_http() -> None:
    with anyio.fail_after(15):
        async with e2e_client("http") as (client, _port):
            result = await client.call_tool(
                "leaderboard",
                {"format": "Test", "metric": "innings_per_hundred", "ground": "Dubai"},
            )

    assert result.structured_content["status"] == "needs_clarification"
    assert "Dubai Sports City Cricket Stadium" in result.content[0].text
    assert "ICC Academy, Dubai" in result.content[0].text


async def test_e2e_invalid_query_and_too_broad_errors_over_http() -> None:
    with anyio.fail_after(15):
        async with e2e_client("http") as (client, _port):
            invalid = await client.call_tool(
                "query_stats",
                {
                    "query": {
                        "class": 2,
                        "type": "batting",
                        "qualifications": [{"field": "not_a_field", "minimum": 1}],
                    }
                },
            )
        async with e2e_client("http", "broad") as (client, _port):
            broad = await client.call_tool(
                "leaderboard",
                {"format": "ODI", "metric": "innings_per_hundred", "minimum": 1},
            )

    assert invalid.is_error is True
    assert "not_a_field" in invalid.content[0].text
    assert broad.is_error is True
    assert "too broad" in broad.content[0].text
    assert "limit is 1" in broad.content[0].text


async def test_e2e_block_pause_short_circuits_uncached_fetches_and_allows_cache() -> None:
    with anyio.fail_after(15):
        async with e2e_client("http", "block", audit=True) as (client, port):
            first = await client.call_tool("find_player", {"name": "Babar Azam", "format": "ODI"})
            blocked = await client.call_tool(
                "leaderboard",
                {"format": "ODI", "metric": "innings_per_hundred", "minimum": 10},
            )
            paused = await client.call_tool(
                "leaderboard",
                {"format": "ODI", "metric": "innings_per_hundred", "minimum": 10},
            )
            cached = await client.call_tool("find_player", {"name": "Babar Azam", "format": "ODI"})
            assert port is not None
            audit = await _http_audit(port)

    assert first.structured_content["status"] == "match"
    assert blocked.is_error is True
    assert "blocked" in blocked.content[0].text
    assert paused.is_error is True
    assert "paused until 12:01 (UTC+00:00)" in paused.content[0].text
    assert cached.structured_content["status"] == "match"
    assert len(audit["requests"]) == 2
    assert audit["requests"][0].endswith("search=Babar+Azam;template=analysis")


async def test_e2e_retry_after_past_budget_errors_over_http() -> None:
    with anyio.fail_after(15):
        async with e2e_client("http", "retry-after", audit=True) as (client, port):
            result = await client.call_tool(
                "leaderboard",
                {"format": "ODI", "metric": "innings_per_hundred", "minimum": 10},
            )
            assert port is not None
            audit = await _http_audit(port)

    assert result.is_error is True
    assert "try again after 14:47 (UTC+00:00)" in result.content[0].text
    assert len(audit["requests"]) == 1


def _empty_runtime_root(label: str) -> tuple[Path, dict[str, str], list[Path]]:
    root = REPO / ".local" / "e2e-no-files" / f"{label}-{uuid4().hex}"
    names = [
        "cwd",
        "home",
        "userprofile",
        "appdata",
        "localappdata",
        "tmp",
        "xdg-config",
        "xdg-cache",
        "xdg-data",
        "xdg-state",
    ]
    dirs = {name: root / name for name in names}
    for path in dirs.values():
        path.mkdir(parents=True)
    env = launcher_env(
        {
            "HOME": str(dirs["home"]),
            "USERPROFILE": str(dirs["userprofile"]),
            "APPDATA": str(dirs["appdata"]),
            "LOCALAPPDATA": str(dirs["localappdata"]),
            "TMP": str(dirs["tmp"]),
            "TEMP": str(dirs["tmp"]),
            "TMPDIR": str(dirs["tmp"]),
            "XDG_CONFIG_HOME": str(dirs["xdg-config"]),
            "XDG_CACHE_HOME": str(dirs["xdg-cache"]),
            "XDG_DATA_HOME": str(dirs["xdg-data"]),
            "XDG_STATE_HOME": str(dirs["xdg-state"]),
        }
    )
    return root, env, list(dirs.values())


def _assert_empty(paths: list[Path]) -> None:
    non_empty = {
        str(path): [str(child) for child in path.iterdir()] for path in paths if any(path.iterdir())
    }
    assert non_empty == {}


@pytest.mark.parametrize("transport", ["http", "stdio"])
async def test_e2e_full_sessions_write_no_files(transport: str) -> None:
    root, env, dirs = _empty_runtime_root(transport)
    try:
        with anyio.fail_after(25):
            async with e2e_client(
                transport,
                "audit",
                audit=True,
                cwd=root / "cwd",
                env=env,
            ) as (client, port):
                await call_happy_tools(client, expect_exact_tools=False)
                if transport == "stdio":
                    audit_result = await client.call_tool("e2e_audit_report", {})
                    audit = audit_result.structured_content
                else:
                    assert port is not None
                    audit = await _http_audit(port)
        assert audit["events"] == []
        _assert_empty(dirs)
    finally:
        shutil.rmtree(root, ignore_errors=True)
