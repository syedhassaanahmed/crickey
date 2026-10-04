from __future__ import annotations

import socket

import pytest

from crickey import __version__, cli
from crickey.cli import main


def test_help_lists_subcommands(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "{serve,stdio}" in output


def test_plain_command_behaves_like_serve(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[object, int]] = []

    def fake_create_server(settings):
        return object()

    def fake_serve_http(server, settings):
        calls.append((server, settings.port))

    monkeypatch.setattr(cli, "create_server", fake_create_server)
    monkeypatch.setattr(cli, "serve_http", fake_serve_http)

    assert main([]) == 0
    assert main(["serve", "--port", "9000"]) == 0
    assert [port for _, port in calls] == [8765, 9000]


def test_stdio_runs_server(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []

    def fake_create_server(settings):
        return object()

    def fake_run_stdio(server):
        calls.append(server)

    monkeypatch.setattr(cli, "create_server", fake_create_server)
    monkeypatch.setattr(cli, "run_stdio", fake_run_stdio)

    assert main(["stdio"]) == 0
    assert len(calls) == 1


def test_port_in_use_is_clear(capsys: pytest.CaptureFixture[str]) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        port = sock.getsockname()[1]

        assert main(["serve", "--port", str(port)]) == 1

    error = capsys.readouterr().err
    assert f"Port {port} is already in use" in error
    assert "--port or CRICKEY_PORT" in error
    assert "Traceback" not in error


def test_package_version_is_exposed() -> None:
    assert __version__ == "0.1.0"
