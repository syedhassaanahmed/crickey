from __future__ import annotations

import logging
import sys

import pytest

from crickey import __version__, cli
from crickey.cli import main
from crickey.transport import NATIVE_HOST, TransportError, _bind_socket

pytestmark = pytest.mark.usefixtures("stub_cli_transports")


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


def test_port_in_use_is_clear(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    with _bind_socket(NATIVE_HOST, 0) as sock:
        sock.listen()
        port = sock.getsockname()[1]

        def port_in_use(_server, _settings) -> None:
            raise TransportError(
                f"Port {port} is already in use; choose another port with --port or CRICKEY_PORT."
            )

        monkeypatch.setattr(cli, "serve_http", port_in_use)

        assert main(["serve", "--port", str(port)]) == 1
        with pytest.raises(TransportError, match=f"Port {port} is already in use"):
            _bind_socket(NATIVE_HOST, port)

    error = capsys.readouterr().err
    assert f"Port {port} is already in use" in error
    assert "--port or CRICKEY_PORT" in error
    assert "Traceback" not in error


def test_cli_errors_do_not_propagate_to_root_logger(capsys: pytest.CaptureFixture[str]) -> None:
    root = logging.getLogger()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("ROOT: %(message)s"))
    root.addHandler(handler)
    try:
        assert main(["--port", "0"]) == 2
    finally:
        root.removeHandler(handler)

    error = capsys.readouterr().err
    assert error.count("--port='0' is invalid") == 1
    assert "ROOT:" not in error


def test_package_version_is_exposed() -> None:
    assert __version__ == "0.1.0"
