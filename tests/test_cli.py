from __future__ import annotations

import logging
import os
import socket
import sys

import pytest

from crickey.cli import main
from crickey.transport import NATIVE_HOST, TransportError, _bind_socket


def test_help_lists_subcommands(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "{serve,stdio}" in output


def test_port_in_use_is_clear(capsys: pytest.CaptureFixture[str]) -> None:
    with _bind_socket(NATIVE_HOST, 0) as sock:
        sock.listen()
        port = sock.getsockname()[1]
        # Checked before main, which would otherwise go on serving if binding the port worked.
        with pytest.raises(TransportError, match=f"Port {port} is already in use"):
            _bind_socket(NATIVE_HOST, port)

        assert main(["serve", "--port", str(port)]) == 1

    error = capsys.readouterr().err
    assert f"Port {port} is already in use" in error
    assert "--port or CRICKEY_PORT" in error
    assert "Traceback" not in error


def test_bound_socket_reuses_address_only_on_posix() -> None:
    with _bind_socket(NATIVE_HOST, 0) as sock:
        reuse = sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR)

    assert bool(reuse) == (os.name == "posix")


@pytest.mark.usefixtures("stub_cli_transports")
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
