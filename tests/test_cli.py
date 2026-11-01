from __future__ import annotations

import pytest

from crickey.cli import main
from crickey.transport import NATIVE_HOST, TransportError, _bind_socket


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
