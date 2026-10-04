from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import anyio
import pytest
from e2e_pages import (
    AMBIGUOUS_NAME,
    COMPARISON_PROOF_URL,
    LEADERBOARD_PROOF_URL,
    PLAYER_RECORD_PROOF_URL,
    PRIMARY_ID,
    PRIMARY_NAME,
    QUERY_STATS_URL,
    RECENT_MATCH_LINE,
    SECOND_CANDIDATE,
    SECOND_ID,
    player_profile_url,
)
from helpers import free_port
from mcp import Client
from mcp.client.stdio import StdioServerParameters, stdio_client

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
PYTHON = sys.executable


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class RuntimeDirs:
    def __init__(self, root: Path) -> None:
        self.root = root
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
        self.dirs = {name: root / name for name in names}
        for path in self.dirs.values():
            path.mkdir(parents=True)
        self.audit_stderr = root / "audit-stderr.txt"

    @property
    def cwd(self) -> Path:
        return self.dirs["cwd"]

    def env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        paths = [str(REPO / "src"), str(REPO / "tests")]
        if env.get("PYTHONPATH"):
            paths.append(env["PYTHONPATH"])
        env["PYTHONPATH"] = os.pathsep.join(paths)
        env.update(
            {
                "HOME": str(self.dirs["home"]),
                "USERPROFILE": str(self.dirs["userprofile"]),
                "APPDATA": str(self.dirs["appdata"]),
                "LOCALAPPDATA": str(self.dirs["localappdata"]),
                "TMP": str(self.dirs["tmp"]),
                "TEMP": str(self.dirs["tmp"]),
                "TMPDIR": str(self.dirs["tmp"]),
                "XDG_CONFIG_HOME": str(self.dirs["xdg-config"]),
                "XDG_CACHE_HOME": str(self.dirs["xdg-cache"]),
                "XDG_DATA_HOME": str(self.dirs["xdg-data"]),
                "XDG_STATE_HOME": str(self.dirs["xdg-state"]),
            }
        )
        return env

    def assert_empty(self) -> None:
        non_empty = {
            str(path): [str(child) for child in path.iterdir()]
            for path in self.dirs.values()
            if any(path.iterdir())
        }
        assert non_empty == {}


def parse_audit_stderr(path: Path) -> dict[str, object]:
    lines = path.read_text(encoding="utf-8") if path.exists() else ""
    for line in reversed(lines.splitlines()):
        if line.startswith("CRICKEY_E2E_AUDIT "):
            return json.loads(line.removeprefix("CRICKEY_E2E_AUDIT "))
    raise AssertionError(f"audit line not found in {path}:\n{lines}")


def _wait_for_health(port: int, process: subprocess.Popen[bytes], stderr_path: Path) -> None:
    deadline = time.monotonic() + 12
    url = f"http://127.0.0.1:{port}/health"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
            raise RuntimeError(
                f"e2e server exited early with {process.returncode}\nSTDERR:\n{stderr}"
            )
        try:
            with urllib.request.urlopen(url, timeout=0.25) as response:
                if response.read() == b'{"status":"ok"}':
                    return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("e2e server did not become healthy")


def _wait_for_port_closed(port: int) -> None:
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.2):
                time.sleep(0.05)
        except OSError:
            return
    raise AssertionError(f"port {port} stayed open after shutdown")


def _kill_process_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return
    process.kill()


class HttpServer:
    def __init__(self, scenario: str, runtime: RuntimeDirs) -> None:
        self.scenario = scenario
        self.runtime = runtime
        self.port = free_port()
        self.process: subprocess.Popen[bytes] | None = None
        self._stderr_handle = None

    def start(self) -> None:
        try:
            self._stderr_handle = self.runtime.audit_stderr.open("wb")
            self.process = subprocess.Popen(
                [
                    PYTHON,
                    "-B",
                    str(LAUNCHER),
                    "http",
                    "--port",
                    str(self.port),
                    "--scenario",
                    self.scenario,
                    "--audit",
                    "--audit-stderr",
                ],
                cwd=self.runtime.cwd,
                env=self.runtime.env(),
                stdout=subprocess.DEVNULL,
                stderr=self._stderr_handle,
            )
            _wait_for_health(self.port, self.process, self.runtime.audit_stderr)
        except Exception:
            if self.process is not None:
                _kill_process_tree(self.process)
                self.process.wait(timeout=8)
                _wait_for_port_closed(self.port)
            if self._stderr_handle is not None:
                self._stderr_handle.close()
            raise

    def stop(self) -> dict[str, object]:
        assert self.process is not None
        try:
            if self.process.poll() is None:
                request = urllib.request.Request(
                    f"http://127.0.0.1:{self.port}/__e2e_shutdown", method="POST"
                )
                with urllib.request.urlopen(request, timeout=3):
                    pass
                _wait_for_port_closed(self.port)
                self.process.wait(timeout=8)
        finally:
            if self.process.poll() is None:
                _kill_process_tree(self.process)
                self.process.wait(timeout=8)
            if self._stderr_handle is not None:
                self._stderr_handle.close()
        return parse_audit_stderr(self.runtime.audit_stderr)


@pytest.fixture(scope="module")
def shared_http_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[HttpServer]:
    runtime = RuntimeDirs(tmp_path_factory.mktemp("crickey-e2e-http"))
    server = HttpServer("happy", runtime)
    server.start()
    try:
        yield server
    finally:
        audit = server.stop()
        assert audit["events"] == []
        runtime.assert_empty()


def test_http_start_failure_cleans_process_and_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def never_healthy(port: int, process: subprocess.Popen[bytes], _stderr_path: Path) -> None:
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("server exited before mutant health failure")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.25):
                    raise RuntimeError("mutant health check never succeeded")
            except OSError:
                time.sleep(0.05)
        raise RuntimeError("server did not listen before mutant health failure")

    monkeypatch.setattr(sys.modules[__name__], "_wait_for_health", never_healthy)
    server = HttpServer("happy", RuntimeDirs(tmp_path / "failed-start-runtime"))

    with pytest.raises(RuntimeError, match="mutant health check never succeeded"):
        server.start()

    assert server.process is not None
    assert server.process.poll() is not None
    assert server._stderr_handle is not None
    assert server._stderr_handle.closed
    _wait_for_port_closed(server.port)


async def _http_audit(port: int) -> dict[str, object]:
    def fetch() -> dict[str, object]:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/__e2e_audit", timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))

    return await asyncio.to_thread(fetch)


async def _http_clear(port: int) -> None:
    def clear() -> None:
        request = urllib.request.Request(f"http://127.0.0.1:{port}/__e2e_clear", method="POST")
        with urllib.request.urlopen(request, timeout=3):
            pass

    await asyncio.to_thread(clear)


@contextmanager
def transient_http_server(
    scenario: str, tmp_path: Path
) -> Iterator[tuple[HttpServer, dict[str, object]]]:
    runtime = RuntimeDirs(tmp_path)
    server = HttpServer(scenario, runtime)
    server.start()
    audit: dict[str, object] = {}
    try:
        yield server, audit
    finally:
        audit.update(server.stop())


def assert_answer_proof(result, url: str, *, confirmed: bool) -> None:
    proof = result.structured_content["proof"]
    assert proof["url"] == url
    assert proof["confirmed"] is confirmed
    assert f"]({url})" in result.structured_content["answer_markdown"]


def assert_profile_links(result, expected: dict[str, int]) -> None:
    answer = result.structured_content["answer_markdown"]
    for name, player_id in expected.items():
        assert f"]({player_profile_url(player_id)})" in answer, name


def assert_full_freshness(result) -> None:
    answer = result.structured_content["answer_markdown"]
    assert RECENT_MATCH_LINE in answer


async def call_happy_tools(client: Client, *, expect_exact_tools: bool = True) -> dict[str, object]:
    tools = (await client.list_tools()).tools
    names = {tool.name for tool in tools}
    if expect_exact_tools:
        assert names == TOOL_NAMES
    else:
        assert TOOL_NAMES <= names

    find = await client.call_tool("find_player", {"name": PRIMARY_NAME, "format": "ODI"})
    ambiguous_find = await client.call_tool(
        "find_player", {"name": AMBIGUOUS_NAME, "format": "T20I"}
    )
    leaderboard = await client.call_tool(
        "leaderboard",
        {"format": "ODI", "metric": "innings_per_hundred", "minimum": 10, "top_n": 2},
    )
    comparison = await client.call_tool(
        "better_than_player",
        {"player_name": PRIMARY_NAME, "format": "T20I", "metrics": ["average", "strike_rate"]},
    )
    record = await client.call_tool(
        "player_record", {"player_name": PRIMARY_NAME, "format": "ODI", "trophy": "World Cup"}
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
    assert ambiguous_find.structured_content["status"] == "needs_clarification"
    assert {candidate["name"] for candidate in ambiguous_find.structured_content["candidates"]} == {
        PRIMARY_NAME,
        SECOND_CANDIDATE,
    }
    candidate_ids = {
        candidate["name"]: candidate["id"]
        for candidate in ambiguous_find.structured_content["candidates"]
    }
    assert candidate_ids == {PRIMARY_NAME: PRIMARY_ID, SECOND_CANDIDATE: SECOND_ID}
    assert leaderboard.structured_content["status"] == "ok"
    assert comparison.structured_content["status"] == "ok"
    assert record.structured_content["status"] == "ok"
    assert query_stats.structured_content["total"] == 1
    assert query_stats.structured_content["rows"][0]["Player"] == "Taro Moss (QQQ)"
    assert query_stats.structured_content["link"] == QUERY_STATS_URL
    assert query_stats.structured_content["freshness"] == RECENT_MATCH_LINE

    assert_answer_proof(leaderboard, LEADERBOARD_PROOF_URL, confirmed=False)
    assert_answer_proof(comparison, COMPARISON_PROOF_URL, confirmed=True)
    assert_answer_proof(record, PLAYER_RECORD_PROOF_URL, confirmed=True)
    assert_profile_links(leaderboard, {"Aster Finch": 910101, PRIMARY_NAME: PRIMARY_ID})
    assert_profile_links(
        comparison,
        {"Cedar Rune": 910201, "Mira Sol": 910202, PRIMARY_NAME: PRIMARY_ID},
    )
    assert_profile_links(record, {PRIMARY_NAME: PRIMARY_ID})
    for result in (leaderboard, comparison, record):
        assert "As of: 4 Oct 2026" in result.structured_content["answer_markdown"]
        assert_full_freshness(result)

    return {
        "find_player": find,
        "ambiguous_find_player": ambiguous_find,
        "leaderboard": leaderboard,
        "better_than_player": comparison,
        "player_record": record,
        "query_stats": query_stats,
    }


async def test_http_mcp_client_calls_tools_clarifies_and_checks_links(
    shared_http_server: HttpServer,
) -> None:
    with anyio.fail_after(20):
        async with Client(
            f"http://127.0.0.1:{shared_http_server.port}/mcp", read_timeout_seconds=6
        ) as client:
            results = await call_happy_tools(client)

    assert results["leaderboard"].structured_content["rows"][0]["player"] == "Aster Finch"
    assert results["better_than_player"].structured_content["beaters"][0]["player"] == "Cedar Rune"
    assert results["player_record"].structured_content["row"]["100"] == 2


async def test_stdio_mcp_client_calls_tools_clarifies_and_writes_no_files(
    tmp_path: Path,
) -> None:
    runtime = RuntimeDirs(tmp_path / "stdio-runtime")
    params = StdioServerParameters(
        command=PYTHON,
        args=["-B", str(LAUNCHER), "stdio", "--scenario", "happy", "--audit", "--audit-stderr"],
        cwd=runtime.cwd,
        env=runtime.env(),
    )
    with runtime.audit_stderr.open("w+", encoding="utf-8") as errlog:
        with anyio.fail_after(25):
            async with Client(
                stdio_client(params, errlog=errlog), read_timeout_seconds=6
            ) as client:
                await call_happy_tools(client, expect_exact_tools=False)
    audit = parse_audit_stderr(runtime.audit_stderr)
    assert audit["events"] == []
    runtime.assert_empty()


async def test_e2e_filter_clarification_and_errors_over_http(
    shared_http_server: HttpServer,
) -> None:
    with anyio.fail_after(15):
        async with Client(
            f"http://127.0.0.1:{shared_http_server.port}/mcp", read_timeout_seconds=6
        ) as client:
            filter_result = await client.call_tool(
                "leaderboard",
                {"format": "Test", "metric": "innings_per_hundred", "ground": "Novel Ground"},
            )
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
            broad = await client.call_tool(
                "leaderboard",
                {"format": "ODI", "metric": "innings_per_hundred", "minimum": 1},
            )

    assert filter_result.structured_content["status"] == "needs_clarification"
    assert "North Novel Ground" in filter_result.content[0].text
    assert "South Novel Ground" in filter_result.content[0].text
    assert invalid.is_error is True
    assert "not_a_field" in invalid.content[0].text
    assert broad.is_error is True
    assert "too broad" in broad.content[0].text
    assert "limit is 4" in broad.content[0].text


async def test_e2e_full_http_session_writes_no_files(shared_http_server: HttpServer) -> None:
    audit = await _http_audit(shared_http_server.port)
    assert audit["events"] == []


async def test_e2e_block_pause_short_circuits_uncached_fetches_and_allows_cache(
    tmp_path: Path,
) -> None:
    with transient_http_server("block", tmp_path / "block-runtime") as (server, audit_at_exit):
        with anyio.fail_after(15):
            async with Client(
                f"http://127.0.0.1:{server.port}/mcp", read_timeout_seconds=6
            ) as client:
                first = await client.call_tool(
                    "find_player", {"name": PRIMARY_NAME, "format": "ODI"}
                )
                blocked = await client.call_tool(
                    "leaderboard",
                    {"format": "ODI", "metric": "innings_per_hundred", "minimum": 10},
                )
                paused = await client.call_tool(
                    "leaderboard",
                    {"format": "ODI", "metric": "innings_per_hundred", "minimum": 10},
                )
                cached = await client.call_tool(
                    "find_player", {"name": PRIMARY_NAME, "format": "ODI"}
                )
                audit = await _http_audit(server.port)

    assert first.structured_content["status"] == "match"
    assert blocked.is_error is True
    assert "blocked" in blocked.content[0].text
    assert paused.is_error is True
    assert "paused until 12:06 (UTC+00:00)" in paused.content[0].text
    assert cached.structured_content["status"] == "match"
    assert len(audit["requests"]) == 2
    assert audit["requests"][0].endswith(
        f"search={PRIMARY_NAME.replace(' ', '+')};template=analysis"
    )
    assert audit_at_exit["events"] == []


async def test_e2e_retry_after_budget_paths_over_http(tmp_path: Path) -> None:
    with transient_http_server("retry-after", tmp_path / "retry-runtime") as (
        server,
        audit_at_exit,
    ):
        with anyio.fail_after(20):
            async with Client(
                f"http://127.0.0.1:{server.port}/mcp", read_timeout_seconds=6
            ) as client:
                success = await client.call_tool(
                    "leaderboard",
                    {"format": "ODI", "metric": "innings_per_hundred", "minimum": 11},
                )
                success_audit = await _http_audit(server.port)
                await _http_clear(server.port)
                past = await client.call_tool(
                    "leaderboard",
                    {"format": "ODI", "metric": "innings_per_hundred", "minimum": 10},
                )
                past_audit = await _http_audit(server.port)

    assert success.is_error is False
    assert success.structured_content["rows"][0]["player"] == "Retry Winner"
    assert success_audit["sleeps"] == [30.0]
    assert past.is_error is True
    assert "try again after 14:48 (UTC+00:00)" in past.content[0].text
    assert past_audit["sleeps"] == []
    assert len(past_audit["requests"]) == 1
    assert audit_at_exit["events"] == []
