from __future__ import annotations

import argparse
import asyncio
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from contextlib import closing

from mcp import Client
from mcp.client.stdio import StdioServerParameters

EXPECTED_TOOLS = {
    "better_than_player",
    "find_player",
    "leaderboard",
    "player_record",
    "query_stats",
}
HEALTH_BODY = b'{"status":"ok"}'
DOCKER_TIMEOUT_SECONDS = 30
HTTP_READY_TIMEOUT_SECONDS = 30
STOP_GRACE_SECONDS = 10


class SmokeError(RuntimeError):
    pass


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke-test a crickey Docker image.")
    parser.add_argument("image", help="Docker image reference to test")
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(smoke_test(args.image))
    except SmokeError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    except Exception as error:  # pragma: no cover - keeps script failures concise
        print(f"FAIL: unexpected error: {error}", file=sys.stderr)
        return 1

    print(f"PASS: HTTP and stdio smoke tests passed for {args.image}")
    print(f"STOP_SECONDS={result['stop_seconds']:.3f}")
    return 0


async def smoke_test(image: str) -> dict[str, float]:
    assert_non_root(image)
    port = free_port()
    container_id = start_http_container(image, port)
    stop_seconds = 0.0
    try:
        wait_for_health(port)
        await check_http_tools(port)
    finally:
        stop_seconds = stop_container(container_id)

    if stop_seconds >= STOP_GRACE_SECONDS - 0.25:
        raise SmokeError(
            f"docker stop took {stop_seconds:.3f}s, the full {STOP_GRACE_SECONDS}s grace period"
        )

    await check_stdio_tools(image)
    return {"stop_seconds": stop_seconds}


def run(
    args: Sequence[str],
    *,
    input_text: str | None = None,
    timeout: float = DOCKER_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            args,
            input=input_text,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise SmokeError(f"{' '.join(args)} timed out after {timeout}s") from error
    if completed.returncode != 0:
        details = (completed.stderr or completed.stdout).strip()
        raise SmokeError(f"{' '.join(args)} failed with exit {completed.returncode}: {details}")
    return completed


def assert_non_root(image: str) -> None:
    completed = run(["docker", "run", "--rm", "--entrypoint", "id", image, "-u"])
    uid = completed.stdout.strip()
    if uid == "0":
        raise SmokeError("container runs as root (id -u returned 0)")
    if not uid.isdigit():
        raise SmokeError(f"could not parse container uid from id -u output: {uid!r}")


def free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def start_http_container(image: str, port: int) -> str:
    completed = run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--read-only",
            "-p",
            f"127.0.0.1:{port}:8765",
            image,
        ]
    )
    container_id = completed.stdout.strip()
    if not container_id:
        raise SmokeError("docker run did not return a container id")
    return container_id


def health_body(port: int) -> bytes:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:
        return response.read()


def wait_for_health(port: int) -> None:
    deadline = time.monotonic() + HTTP_READY_TIMEOUT_SECONDS
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            body = health_body(port)
            if body == HEALTH_BODY:
                return
            raise SmokeError(f"/health returned {body!r}, expected {HEALTH_BODY!r}")
        except (ConnectionError, TimeoutError, urllib.error.URLError) as error:
            last_error = error
            time.sleep(0.2)
    raise SmokeError(f"/health did not become ready: {last_error}")


async def check_http_tools(port: int) -> None:
    async with Client(f"http://127.0.0.1:{port}/mcp", read_timeout_seconds=10) as client:
        tools = (await client.list_tools()).tools
        assert_tool_names([tool.name for tool in tools])
        result = await client.call_tool(
            "query_stats",
            {"query": {"class": 2, "type": "batting"}, "fetch": False},
        )
    if result.is_error:
        raise SmokeError(f"query_stats(fetch=false) returned an error: {result}")
    link = result.structured_content.get("link")
    if not isinstance(link, str) or "https://stats.cricinfo.com/" not in link:
        raise SmokeError(f"query_stats(fetch=false) did not return a Statsguru link: {link!r}")


async def check_stdio_tools(image: str) -> None:
    params = StdioServerParameters(
        command="docker",
        args=["run", "-i", "--rm", "--read-only", image, "stdio"],
    )
    try:
        async with asyncio.timeout(20):
            async with Client(params, read_timeout_seconds=10) as client:
                tools = (await client.list_tools()).tools
    except TimeoutError as error:
        raise SmokeError("stdio tool listing timed out") from error
    assert_tool_names([tool.name for tool in tools])


def assert_tool_names(names: Sequence[str]) -> None:
    found = set(names)
    if found != EXPECTED_TOOLS:
        raise SmokeError(f"tool names were {sorted(found)}, expected {sorted(EXPECTED_TOOLS)}")


def stop_container(container_id: str) -> float:
    start = time.monotonic()
    try:
        run(["docker", "stop", "--time", str(STOP_GRACE_SECONDS), container_id], timeout=20)
    except SmokeError as error:
        raise SmokeError(f"failed to stop HTTP container {container_id}: {error}") from error
    return time.monotonic() - start


if __name__ == "__main__":
    raise SystemExit(main())
