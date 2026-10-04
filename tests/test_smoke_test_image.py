from __future__ import annotations

import asyncio
import importlib.util
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
    validate_query_stats_result(
        is_error=False,
        structured={
            "link": (
                "https://stats.cricinfo.com/ci/engine/player/index.html?"
                "class=2;spanmax1=04+Oct+2026;spanmin1=05+Jan+1971;type=batting"
            ),
            "fetch": False,
            "total": None,
            "rows": [],
        },
    )

    with pytest.raises(SmokeError, match="unpinned"):
        validate_query_stats_result(
            is_error=False,
            structured={
                "link": "https://stats.cricinfo.com/ci/engine/player/index.html?class=2",
                "fetch": False,
                "total": None,
                "rows": [],
            },
        )


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


def test_startup_crash_reports_original_logs_and_removes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    removed: list[str] = []
    monkeypatch.setattr(smoke_test_image, "assert_non_root", lambda _image: None)
    monkeypatch.setattr(smoke_test_image, "free_port", lambda: 9876)
    monkeypatch.setattr(smoke_test_image, "start_http_container", lambda _image, _port: "cid")
    monkeypatch.setattr(
        smoke_test_image,
        "wait_for_health",
        lambda _port, _cid: (_ for _ in ()).throw(
            SmokeError("HTTP container exited before /health became ready: bad port")
        ),
    )
    monkeypatch.setattr(smoke_test_image, "container_is_running", lambda _cid: False)
    monkeypatch.setattr(smoke_test_image, "container_logs", lambda _cid: "Container logs: boom")
    monkeypatch.setattr(smoke_test_image, "remove_container", removed.append)

    with pytest.raises(SmokeError) as error:
        asyncio.run(smoke_test_image.smoke_test("image"))

    assert str(error.value).startswith("HTTP container exited before /health became ready")
    assert "Container logs: boom" in str(error.value)
    assert removed == ["cid"]


def test_failed_check_reports_unwrapped_error_logs_and_removes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    removed: list[str] = []
    stopped: list[str] = []
    monkeypatch.setattr(smoke_test_image, "assert_non_root", lambda _image: None)
    monkeypatch.setattr(smoke_test_image, "free_port", lambda: 9876)
    monkeypatch.setattr(smoke_test_image, "start_http_container", lambda _image, _port: "cid")
    monkeypatch.setattr(smoke_test_image, "wait_for_health", lambda _port, _cid: None)

    async def fail_check(_port: int) -> None:
        raise ExceptionGroup("unhandled errors in a TaskGroup", [RuntimeError("real MCP error")])

    monkeypatch.setattr(smoke_test_image, "check_http_tools", fail_check)
    monkeypatch.setattr(smoke_test_image, "container_is_running", lambda _cid: True)
    monkeypatch.setattr(smoke_test_image, "stop_container", lambda cid: stopped.append(cid) or 0.1)
    monkeypatch.setattr(smoke_test_image, "container_logs", lambda _cid: "Container logs: mcp log")
    monkeypatch.setattr(smoke_test_image, "remove_container", removed.append)

    with pytest.raises(SmokeError) as error:
        asyncio.run(smoke_test_image.smoke_test("image"))

    message = str(error.value)
    assert message.startswith("real MCP error")
    assert "unhandled errors in a TaskGroup" not in message
    assert "Container logs: mcp log" in message
    assert stopped == ["cid"]
    assert removed == ["cid"]


def test_slow_stop_is_reported_and_container_is_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    removed: list[str] = []
    monkeypatch.setattr(smoke_test_image, "assert_non_root", lambda _image: None)
    monkeypatch.setattr(smoke_test_image, "free_port", lambda: 9876)
    monkeypatch.setattr(smoke_test_image, "start_http_container", lambda _image, _port: "cid")
    monkeypatch.setattr(smoke_test_image, "wait_for_health", lambda _port, _cid: None)

    async def ok_check(_port: int) -> None:
        return None

    monkeypatch.setattr(smoke_test_image, "check_http_tools", ok_check)
    monkeypatch.setattr(smoke_test_image, "container_is_running", lambda _cid: True)
    monkeypatch.setattr(smoke_test_image, "stop_container", lambda _cid: 10.0)
    monkeypatch.setattr(smoke_test_image, "remove_container", removed.append)

    with pytest.raises(SmokeError, match="full 10s grace period"):
        asyncio.run(smoke_test_image.smoke_test("image"))

    assert removed == ["cid"]
