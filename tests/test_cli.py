from __future__ import annotations

import pytest

from crickey import __version__
from crickey.cli import main


def test_help_lists_subcommands(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "{serve,stdio}" in output


def test_plain_command_behaves_like_serve(capsys: pytest.CaptureFixture[str]) -> None:
    plain_status = main([])
    plain_error = capsys.readouterr().err

    serve_status = main(["serve"])
    serve_error = capsys.readouterr().err

    assert plain_status == serve_status == 1
    assert plain_error == serve_error
    assert "#10" in plain_error


def test_stdio_placeholder_mentions_issue(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["stdio"]) == 1
    error = capsys.readouterr().err
    assert "crickey stdio" in error
    assert "#10" in error


def test_package_version_is_exposed() -> None:
    assert __version__ == "0.1.0"
