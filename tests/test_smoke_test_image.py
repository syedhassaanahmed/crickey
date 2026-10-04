from __future__ import annotations

import asyncio
import importlib.util
import subprocess
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from crickey.server import create_server
from crickey.settings import Settings
from crickey.transport import NATIVE_HOST, streamable_http_app

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "smoke_test_image.py"
_SPEC = importlib.util.spec_from_file_location("smoke_test_image", _SCRIPT)
assert _SPEC is not None
smoke_test_image = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(smoke_test_image)

HEALTH_BODY = smoke_test_image.HEALTH_BODY
SmokeError = smoke_test_image.SmokeError
assert_tool_names = smoke_test_image.assert_tool_names
validate_query_stats_result = smoke_test_image.validate_query_stats_result


def good_query_payload() -> dict[str, object]:
    return {
        "link": (
            "https://stats.cricinfo.com/ci/engine/player/index.html?"
            "class=2;spanmax1=04+Oct+2026;spanmin1=05+Jan+1971;type=batting"
        ),
        "fetch": False,
        "total": None,
        "rows": [],
    }


def test_assert_tool_names_accepts_exact_set() -> None:
    assert_tool_names(
        [
            "leaderboard",
            "better_than_player",
            "player_record",
            "find_player",
            "query_stats",
        ]
    )


def test_assert_tool_names_rejects_missing_or_extra_tool() -> None:
    with pytest.raises(SmokeError, match="tool names"):
        assert_tool_names(["query_stats", "extra"])


def test_health_body_is_exact_transport_response() -> None:
    app = streamable_http_app(create_server(Settings()), host=NATIVE_HOST)

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.content == HEALTH_BODY


def test_query_stats_result_requires_pinned_no_fetch_payload() -> None:
    validate_query_stats_result(is_error=False, structured=good_query_payload())


@pytest.mark.parametrize(
    ("is_error", "payload_update", "missing_key", "match"),
    [
        (True, {}, None, "returned an error"),
        (False, {"link": "https://example.test/?spanmin1=x;spanmax1=y"}, None, "Statsguru"),
        (
            False,
            {"link": "https://stats.cricinfo.com/ci/engine/player/index.html?spanmin1=x"},
            None,
            "unpinned",
        ),
        (
            False,
            {"link": "https://stats.cricinfo.com/ci/engine/player/index.html?spanmax1=y"},
            None,
            "unpinned",
        ),
        (False, {"fetch": True}, None, "fetch=True"),
        (False, {"total": 0}, None, "total=0"),
        (False, {"rows": [{"Player": "Someone"}]}, None, "rows="),
        (False, {}, "link", "Statsguru link"),
        (False, {}, "fetch", "fetch=None"),
        (False, {}, "total", "total=None"),
        (False, {}, "rows", "rows=None"),
    ],
)
def test_query_stats_result_rejects_each_bad_payload(
    is_error: bool,
    payload_update: dict[str, object],
    missing_key: str | None,
    match: str | None,
) -> None:
    payload = good_query_payload()
    payload.update(payload_update)
    if missing_key is not None:
        del payload[missing_key]

    with pytest.raises(SmokeError, match=match):
        validate_query_stats_result(is_error=is_error, structured=payload)


def test_docker_environment_preserves_docker_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCKER_HOST", "npipe:////./pipe/docker_engine")
    monkeypatch.setenv("DOCKER_CONTEXT", "rootless")
    monkeypatch.setenv("DOCKER_CONFIG", "C:\\Users\\me\\.docker")
    monkeypatch.setenv("DOCKER_CERT_PATH", "C:\\certs")
    monkeypatch.setenv("DOCKER_TLS_VERIFY", "1")
    monkeypatch.setenv("OTHER", "ignored")

    env = smoke_test_image.docker_environment()

    assert env == {
        "DOCKER_HOST": "npipe:////./pipe/docker_engine",
        "DOCKER_CONTEXT": "rootless",
        "DOCKER_CONFIG": "C:\\Users\\me\\.docker",
        "DOCKER_CERT_PATH": "C:\\certs",
        "DOCKER_TLS_VERIFY": "1",
    }


def test_docker_output_includes_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["docker", "logs"],
            returncode=0,
            stdout="stdout log\n",
            stderr="stderr log\n",
        )

    monkeypatch.setattr(smoke_test_image.subprocess, "run", fake_run)

    assert smoke_test_image.docker_output(["logs", "cid"]) == "stdout log\nstderr log\n"


def test_container_logs_returns_log_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(smoke_test_image, "docker_output", lambda _args: "stderr log\n")

    assert smoke_test_image.container_logs("cid") == "Container logs (tail):\nstderr log"


def test_wait_for_health_fails_fast_when_container_exited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    health_calls: list[int] = []
    monkeypatch.setattr(smoke_test_image, "container_is_running", lambda _cid: False)
    monkeypatch.setattr(smoke_test_image, "health_body", lambda port: health_calls.append(port))

    with pytest.raises(SmokeError, match="exited before /health"):
        smoke_test_image.wait_for_health(9876, "cid")

    assert health_calls == []


def test_startup_crash_reports_original_logs_and_removes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    removed: list[str] = []
    monkeypatch.setattr(smoke_test_image, "assert_non_root", lambda _image: None)
    monkeypatch.setattr(smoke_test_image, "free_port", lambda: 9876)
    monkeypatch.setattr(smoke_test_image, "create_http_container", lambda _image, _port: "cid")
    monkeypatch.setattr(smoke_test_image, "start_container", lambda _cid: None)
    monkeypatch.setattr(
        smoke_test_image,
        "wait_for_health",
        lambda _port, _cid: (_ for _ in ()).throw(
            SmokeError("HTTP container exited before /health became ready: bad port")
        ),
    )
    monkeypatch.setattr(smoke_test_image, "container_logs", lambda _cid: "Container logs: boom")
    monkeypatch.setattr(smoke_test_image, "remove_container", removed.append)

    with pytest.raises(SmokeError) as error:
        asyncio.run(smoke_test_image.smoke_test("image"))

    assert str(error.value).startswith(
        "SmokeError: HTTP container exited before /health became ready"
    )
    assert "Container logs: boom" in str(error.value)
    assert removed == ["cid"]


def test_start_failure_removes_created_container(monkeypatch: pytest.MonkeyPatch) -> None:
    removed: list[str] = []
    monkeypatch.setattr(smoke_test_image, "assert_non_root", lambda _image: None)
    monkeypatch.setattr(smoke_test_image, "free_port", lambda: 9876)
    monkeypatch.setattr(smoke_test_image, "create_http_container", lambda _image, _port: "cid")
    monkeypatch.setattr(
        smoke_test_image,
        "start_container",
        lambda _cid: (_ for _ in ()).throw(SmokeError("docker start failed")),
    )
    monkeypatch.setattr(smoke_test_image, "container_logs", lambda _cid: "Container logs: start")
    monkeypatch.setattr(smoke_test_image, "remove_container", removed.append)

    with pytest.raises(SmokeError) as error:
        asyncio.run(smoke_test_image.smoke_test("image"))

    assert "SmokeError: docker start failed" in str(error.value)
    assert "Container logs: start" in str(error.value)
    assert removed == ["cid"]


def test_failed_check_reports_unwrapped_error_logs_and_removes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    removed: list[str] = []
    monkeypatch.setattr(smoke_test_image, "assert_non_root", lambda _image: None)
    monkeypatch.setattr(smoke_test_image, "free_port", lambda: 9876)
    monkeypatch.setattr(smoke_test_image, "create_http_container", lambda _image, _port: "cid")
    monkeypatch.setattr(smoke_test_image, "start_container", lambda _cid: None)
    monkeypatch.setattr(smoke_test_image, "wait_for_health", lambda _port, _cid: None)

    async def fail_check(_port: int) -> None:
        raise ExceptionGroup("unhandled errors in a TaskGroup", [RuntimeError("real MCP error")])

    monkeypatch.setattr(smoke_test_image, "check_http_tools", fail_check)
    monkeypatch.setattr(smoke_test_image, "container_logs", lambda _cid: "Container logs: mcp log")
    monkeypatch.setattr(smoke_test_image, "remove_container", removed.append)

    with pytest.raises(SmokeError) as error:
        asyncio.run(smoke_test_image.smoke_test("image"))

    message = str(error.value)
    assert message.startswith("RuntimeError: real MCP error")
    assert "unhandled errors in a TaskGroup" not in message
    assert "Container logs: mcp log" in message
    assert removed == ["cid"]


def test_slow_stop_is_reported_and_container_is_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    removed: list[str] = []
    monkeypatch.setattr(smoke_test_image, "assert_non_root", lambda _image: None)
    monkeypatch.setattr(smoke_test_image, "free_port", lambda: 9876)
    monkeypatch.setattr(smoke_test_image, "create_http_container", lambda _image, _port: "cid")
    monkeypatch.setattr(smoke_test_image, "start_container", lambda _cid: None)
    monkeypatch.setattr(smoke_test_image, "wait_for_health", lambda _port, _cid: None)

    async def ok_check(_port: int) -> None:
        return None

    monkeypatch.setattr(smoke_test_image, "check_http_tools", ok_check)
    monkeypatch.setattr(smoke_test_image, "stop_http_container", lambda _cid: 10.0)
    monkeypatch.setattr(smoke_test_image, "remove_container", removed.append)

    with pytest.raises(SmokeError, match="full 10s grace period"):
        asyncio.run(smoke_test_image.smoke_test("image"))

    assert removed == ["cid"]


def test_stop_failure_still_removes_container(monkeypatch: pytest.MonkeyPatch) -> None:
    removed: list[str] = []
    monkeypatch.setattr(smoke_test_image, "assert_non_root", lambda _image: None)
    monkeypatch.setattr(smoke_test_image, "free_port", lambda: 9876)
    monkeypatch.setattr(smoke_test_image, "create_http_container", lambda _image, _port: "cid")
    monkeypatch.setattr(smoke_test_image, "start_container", lambda _cid: None)
    monkeypatch.setattr(smoke_test_image, "wait_for_health", lambda _port, _cid: None)

    async def ok_check(_port: int) -> None:
        return None

    monkeypatch.setattr(smoke_test_image, "check_http_tools", ok_check)
    monkeypatch.setattr(
        smoke_test_image,
        "stop_http_container",
        lambda _cid: (_ for _ in ()).throw(SmokeError("docker stop failed")),
    )
    monkeypatch.setattr(smoke_test_image, "container_logs", lambda _cid: "Container logs: stop")
    monkeypatch.setattr(smoke_test_image, "remove_container", removed.append)

    with pytest.raises(SmokeError) as error:
        asyncio.run(smoke_test_image.smoke_test("image"))

    assert "SmokeError: docker stop failed" in str(error.value)
    assert "Container logs: stop" in str(error.value)
    assert removed == ["cid"]


def test_stop_requires_running_and_zero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(smoke_test_image, "container_is_running", lambda _cid: False)
    monkeypatch.setattr(smoke_test_image, "container_exit_code", lambda _cid: 42)

    with pytest.raises(SmokeError, match="not running"):
        smoke_test_image.stop_http_container("cid")

    monkeypatch.setattr(smoke_test_image, "container_is_running", lambda _cid: True)
    monkeypatch.setattr(smoke_test_image, "stop_container", lambda _cid: 0.1)
    monkeypatch.setattr(smoke_test_image, "container_exit_code", lambda _cid: 143)

    with pytest.raises(SmokeError, match="code 143"):
        smoke_test_image.stop_http_container("cid")
