from __future__ import annotations

import logging
from argparse import Namespace
from datetime import timedelta

import pytest

from crickey.cli import build_parser, main
from crickey.settings import Settings, SettingsError, load_settings

pytestmark = pytest.mark.usefixtures("stub_cli_transports")

ENV_NAMES = (
    "CRICKEY_MIN_INTERVAL",
    "CRICKEY_MAX_RETRIES",
    "CRICKEY_BLOCK_PAUSES",
    "CRICKEY_MAX_PAGES",
    "CRICKEY_CACHE_MAX_MB",
    "CRICKEY_RECENT_TTL",
    "CRICKEY_PORT",
    "CRICKEY_IN_CONTAINER",
    "CRICKEY_HOST",
    "CRICKEY_URL",
    "CRICKEY_TOKEN",
)
TOKEN = "0123456789abcdef" * 4


def _settings_from_cli(argv: list[str]) -> Settings:
    args = build_parser().parse_args(argv)
    return load_settings(args, {})


def _empty_args() -> Namespace:
    return Namespace()


def _clear_crickey_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for env_name in ENV_NAMES:
        monkeypatch.delenv(env_name, raising=False)


def test_every_environment_variable_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_crickey_env(monkeypatch)
    monkeypatch.setenv("CRICKEY_MIN_INTERVAL", "20s")
    monkeypatch.setenv("CRICKEY_MAX_RETRIES", "5")
    monkeypatch.setenv("CRICKEY_BLOCK_PAUSES", "1s,2m,3h")
    monkeypatch.setenv("CRICKEY_MAX_PAGES", "7")
    monkeypatch.setenv("CRICKEY_CACHE_MAX_MB", "128")
    monkeypatch.setenv("CRICKEY_RECENT_TTL", "90s")
    monkeypatch.setenv("CRICKEY_PORT", "9999")
    monkeypatch.setenv("CRICKEY_IN_CONTAINER", "yes")
    monkeypatch.setenv("CRICKEY_HOST", "0.0.0.0")
    monkeypatch.setenv("CRICKEY_URL", "HTTPS://Crickey.Example.com:443/, http://localhost:9000")
    monkeypatch.setenv("CRICKEY_TOKEN", TOKEN)

    settings = load_settings(_empty_args())

    assert settings.min_interval == timedelta(seconds=20)
    assert settings.max_retries == 5
    assert settings.block_pauses == (
        timedelta(seconds=1),
        timedelta(minutes=2),
        timedelta(hours=3),
    )
    assert settings.max_pages == 7
    assert settings.cache_max_mb == 128
    assert settings.recent_ttl == timedelta(seconds=90)
    assert settings.port == 9999
    assert settings.in_container is True
    assert settings.host == "0.0.0.0"
    assert settings.urls == ("https://crickey.example.com", "http://localhost:9000")
    assert settings.token == TOKEN


def test_ipv6_host_and_url_flags_are_read() -> None:
    settings = _settings_from_cli(["serve", "--host", "::", "--url", "http://[::1]:8766"])

    assert settings.host == "::"
    assert settings.urls == ("http://[::1]:8766",)


def test_plain_crickey_accepts_serve_flags(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    stub_cli_transports: list[tuple[str, Settings]],
) -> None:
    _clear_crickey_env(monkeypatch)

    assert _settings_from_cli(["--port", "9000"]).port == 9000
    plain_status = main(["--port", "9000"])
    plain_output = capsys.readouterr()
    serve_status = main(["serve", "--port", "9000"])
    serve_output = capsys.readouterr()

    assert plain_status == serve_status == 0
    assert plain_output == serve_output
    assert plain_output.out == ""
    assert plain_output.err == ""
    assert [(name, settings.port) for name, settings in stub_cli_transports] == [
        ("create_server", 9000),
        ("serve_http", 9000),
        ("create_server", 9000),
        ("serve_http", 9000),
    ]


@pytest.mark.parametrize(("raw", "hours"), [("168h", 168), ("604800", 168), ("10080m", 168)])
def test_duration_at_the_168h_cap_is_accepted(raw: str, hours: int) -> None:
    assert load_settings(_empty_args(), {"CRICKEY_RECENT_TTL": raw}).recent_ttl == timedelta(
        hours=hours
    )


@pytest.mark.parametrize(
    ("env_name", "bad_value", "expected"),
    [
        (
            "CRICKEY_MIN_INTERVAL",
            "1s",
            "a positive duration in seconds, or a number with an s, m or h suffix "
            "(examples: 90, 5m, 1h), from 2s through 168h",
        ),
        ("CRICKEY_MAX_RETRIES", "-1", "a non-negative integer"),
        (
            "CRICKEY_BLOCK_PAUSES",
            "",
            "a comma-separated list of positive durations in seconds, or numbers with an s, m "
            "or h suffix (examples: 90, 5m, 1h), each no more than 168h",
        ),
        ("CRICKEY_MAX_PAGES", "-1", "a positive integer"),
        ("CRICKEY_CACHE_MAX_MB", "1.5", "a positive integer"),
        (
            "CRICKEY_RECENT_TTL",
            "soon",
            "a positive duration in seconds, or a number with an s, m or h suffix "
            "(examples: 90, 5m, 1h), no more than 168h",
        ),
        ("CRICKEY_PORT", "65536", "an integer from 1 to 65535"),
        ("CRICKEY_IN_CONTAINER", "maybe", "1/0, true/false, yes/no, or empty"),
        ("CRICKEY_HOST", "localhost", "an IP address, such as 127.0.0.1 or 0.0.0.0"),
        (
            "CRICKEY_URL",
            "crickey.example.com",
            "a comma-separated list of http:// or https:// URLs without a path, "
            "such as https://crickey.example.com",
        ),
    ],
)
def test_invalid_environment_values_name_setting(
    env_name: str,
    bad_value: str,
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_crickey_env(monkeypatch)
    monkeypatch.setenv(env_name, bad_value)

    with pytest.raises(SettingsError) as exc_info:
        load_settings(_empty_args())

    assert str(exc_info.value) == f"{env_name}={bad_value!r} is invalid; expected {expected}."


def test_invalid_value_stops_startup_before_placeholder(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_crickey_env(monkeypatch)
    monkeypatch.setenv("CRICKEY_PORT", "0")

    assert main(["serve"]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert (
        captured.err == "ERROR: CRICKEY_PORT='0' is invalid; expected an integer from 1 to 65535.\n"
    )


@pytest.mark.parametrize("bad_token", ["too-short-secret", TOKEN[:16] + " " + TOKEN[:16]])
def test_token_comes_from_the_environment_and_is_never_shown(
    bad_token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_crickey_env(monkeypatch)
    monkeypatch.setenv("CRICKEY_TOKEN", "")
    assert load_settings(_empty_args()).token is None

    monkeypatch.setenv("CRICKEY_TOKEN", TOKEN)
    settings = load_settings(_empty_args())
    assert settings.token == TOKEN
    assert TOKEN not in repr(settings)

    monkeypatch.setenv("CRICKEY_TOKEN", bad_token)
    with pytest.raises(SettingsError) as exc_info:
        load_settings(_empty_args())
    assert str(exc_info.value) == (
        "CRICKEY_TOKEN is invalid; expected at least 32 characters, with no spaces."
    )

    with pytest.raises(SystemExit):
        build_parser().parse_args(["serve", "--token", TOKEN])


def test_min_interval_floor() -> None:
    assert _settings_from_cli(["serve", "--min-interval", "2s"]).min_interval == timedelta(
        seconds=2
    )

    with pytest.raises(SettingsError) as exc_info:
        _settings_from_cli(["serve", "--min-interval", "1.999s"])

    assert (
        str(exc_info.value)
        == "--min-interval='1.999s' is invalid; expected a positive duration in seconds, "
        "or a number with an s, m or h suffix (examples: 90, 5m, 1h), from 2s through 168h."
    )

    with pytest.raises(SettingsError) as exc_info:
        _settings_from_cli(["serve", "--min-interval", "1m30s"])

    assert (
        str(exc_info.value)
        == "--min-interval='1m30s' is invalid; expected a positive duration in seconds, "
        "or a number with an s, m or h suffix (examples: 90, 5m, 1h), from 2s through 168h."
    )


def test_min_interval_warning_below_fifteen_seconds(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="crickey.settings"):
        settings = _settings_from_cli(["serve", "--min-interval", "14s"])

    assert settings.min_interval == timedelta(seconds=14)
    assert caplog.messages == [
        "--min-interval=14s is below 15s, Cricinfo's crawl delay; requesting faster risks being "
        "blocked."
    ]


@pytest.mark.parametrize(
    "bad_value",
    ["0", "0s", "-1s", "1d", "5min", "1h30m", "", " ", " 5m", "5m "],
)
def test_duration_parsing_edge_cases_are_refused(
    bad_value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_crickey_env(monkeypatch)
    monkeypatch.setenv("CRICKEY_RECENT_TTL", bad_value)

    with pytest.raises(SettingsError) as exc_info:
        load_settings(_empty_args())

    assert (
        str(exc_info.value)
        == f"CRICKEY_RECENT_TTL={bad_value!r} is invalid; expected a positive duration in "
        "seconds, or a number with an s, m or h suffix (examples: 90, 5m, 1h), no more than 168h."
    )


@pytest.mark.parametrize("bad_value", ["99999999999999999", "1000000000h"])
def test_huge_duration_stops_startup_without_traceback(
    bad_value: str,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_crickey_env(monkeypatch)
    monkeypatch.setenv("CRICKEY_RECENT_TTL", bad_value)

    assert main(["serve"]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert (
        captured.err
        == f"ERROR: CRICKEY_RECENT_TTL={bad_value!r} is invalid; expected a positive duration "
        "in seconds, or a number with an s, m or h suffix (examples: 90, 5m, 1h), no more than "
        "168h.\n"
    )
