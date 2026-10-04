from __future__ import annotations

from collections.abc import Generator

import pytest

from crickey import cli
from crickey.settings import Settings


@pytest.fixture
def stub_cli_transports(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[list[tuple[str, Settings]]]:
    calls: list[tuple[str, Settings]] = []

    def fake_create_server(settings: Settings) -> Settings:
        calls.append(("create_server", settings))
        return settings

    def fake_serve_http(server: Settings, settings: Settings) -> None:
        assert server is settings
        calls.append(("serve_http", settings))

    def fake_run_stdio(server: Settings) -> None:
        calls.append(("run_stdio", server))

    monkeypatch.setattr(cli, "create_server", fake_create_server)
    monkeypatch.setattr(cli, "serve_http", fake_serve_http)
    monkeypatch.setattr(cli, "run_stdio", fake_run_stdio)
    yield calls
