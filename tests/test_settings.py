from __future__ import annotations

import logging
from argparse import Namespace
from dataclasses import FrozenInstanceError
from datetime import timedelta

import pytest

from crickey.cli import build_parser, main
from crickey.settings import Settings, SettingsError, load_settings

ENV_NAMES = (
    "CRICKEY_MIN_INTERVAL",
    "CRICKEY_MAX_RETRIES",
    "CRICKEY_BLOCK_PAUSES",
    "CRICKEY_MAX_PAGES",
    "CRICKEY_CACHE_MAX_MB",
    "CRICKEY_RECENT_TTL",
    "CRICKEY_PORT",
    "CRICKEY_IN_CONTAINER",
)


def _settings_from_cli(argv: list[str]) -> Settings:
    args = build_parser().parse_args(argv)
    return load_settings(args, {})


def _empty_args() -> Namespace:
    return Namespace()


def _clear_crickey_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for env_name in ENV_NAMES:
        monkeypatch.delenv(env_name, raising=False)


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_crickey_env(monkeypatch)

    settings = load_settings(_empty_args())

    assert settings == Settings(
        min_interval=timedelta(seconds=15),
        max_retries=3,
        block_pauses=(
            timedelta(minutes=5),
            timedelta(minutes=15),
            timedelta(hours=1),
            timedelta(hours=4),
            timedelta(hours=24),
        ),
        max_pages=4,
        cache_max_mb=64,
        recent_ttl=timedelta(hours=1),
        port=8765,
        in_container=False,
    )


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


def test_every_serve_flag_overrides_default() -> None:
    settings = _settings_from_cli(
        [
            "serve",
            "--min-interval",
            "16",
            "--max-retries",
            "6",
            "--block-pauses",
            "2s,4s",
            "--max-pages",
            "8",
            "--cache-max-mb",
            "256",
            "--recent-ttl",
            "2h",
            "--port",
            "8766",
            "--in-container",
            "true",
        ]
    )

    assert settings.min_interval == timedelta(seconds=16)
    assert settings.max_retries == 6
    assert settings.block_pauses == (timedelta(seconds=2), timedelta(seconds=4))
    assert settings.max_pages == 8
    assert settings.cache_max_mb == 256
    assert settings.recent_ttl == timedelta(hours=2)
    assert settings.port == 8766
    assert settings.in_container is True


def test_every_stdio_flag_overrides_default() -> None:
    settings = _settings_from_cli(
        [
            "stdio",
            "--min-interval",
            "24h",
            "--max-retries",
            "2",
            "--block-pauses",
            "10s",
            "--max-pages",
            "1",
            "--cache-max-mb",
            "1",
            "--recent-ttl",
            "5m",
            "--port",
            "1",
            "--in-container",
            "0",
        ]
    )

    assert settings.min_interval == timedelta(hours=24)
    assert settings.max_retries == 2
    assert settings.block_pauses == (timedelta(seconds=10),)
    assert settings.max_pages == 1
    assert settings.cache_max_mb == 1
    assert settings.recent_ttl == timedelta(minutes=5)
    assert settings.port == 1
    assert settings.in_container is False


def test_flag_wins_over_environment_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_crickey_env(monkeypatch)
    monkeypatch.setenv("CRICKEY_MIN_INTERVAL", "30s")
    monkeypatch.setenv("CRICKEY_MAX_RETRIES", "6")
    monkeypatch.setenv("CRICKEY_BLOCK_PAUSES", "40s")
    monkeypatch.setenv("CRICKEY_MAX_PAGES", "6")
    monkeypatch.setenv("CRICKEY_CACHE_MAX_MB", "66")
    monkeypatch.setenv("CRICKEY_RECENT_TTL", "3h")
    monkeypatch.setenv("CRICKEY_PORT", "8767")
    monkeypatch.setenv("CRICKEY_IN_CONTAINER", "false")
    args = build_parser().parse_args(
        [
            "serve",
            "--min-interval",
            "20s",
            "--max-retries",
            "5",
            "--block-pauses",
            "30s",
            "--max-pages",
            "5",
            "--cache-max-mb",
            "65",
            "--recent-ttl",
            "2h",
            "--port",
            "8766",
            "--in-container",
            "true",
        ]
    )

    settings = load_settings(args)

    assert settings.min_interval == timedelta(seconds=20)
    assert settings.max_retries == 5
    assert settings.block_pauses == (timedelta(seconds=30),)
    assert settings.max_pages == 5
    assert settings.cache_max_mb == 65
    assert settings.recent_ttl == timedelta(hours=2)
    assert settings.port == 8766
    assert settings.in_container is True


@pytest.mark.parametrize(
    ("env_name", "bad_value"),
    [
        ("CRICKEY_MIN_INTERVAL", "1s"),
        ("CRICKEY_MAX_RETRIES", "0"),
        ("CRICKEY_BLOCK_PAUSES", ""),
        ("CRICKEY_MAX_PAGES", "-1"),
        ("CRICKEY_CACHE_MAX_MB", "1.5"),
        ("CRICKEY_RECENT_TTL", "soon"),
        ("CRICKEY_PORT", "65536"),
        ("CRICKEY_IN_CONTAINER", "maybe"),
    ],
)
def test_invalid_environment_values_name_setting(
    env_name: str,
    bad_value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_crickey_env(monkeypatch)
    monkeypatch.setenv(env_name, bad_value)

    with pytest.raises(SettingsError) as exc_info:
        load_settings(_empty_args())

    assert str(exc_info.value).startswith(f"{env_name}={bad_value!r} is invalid; expected ")


@pytest.mark.parametrize(
    ("flag_name", "bad_value"),
    [
        ("--min-interval", "1s"),
        ("--max-retries", "0"),
        ("--block-pauses", ""),
        ("--max-pages", "-1"),
        ("--cache-max-mb", "1.5"),
        ("--recent-ttl", "soon"),
        ("--port", "65536"),
        ("--in-container", "maybe"),
    ],
)
def test_invalid_flag_values_name_setting(flag_name: str, bad_value: str) -> None:
    with pytest.raises(SettingsError) as exc_info:
        _settings_from_cli(["serve", flag_name, bad_value])

    assert str(exc_info.value).startswith(f"{flag_name}={bad_value!r} is invalid; expected ")


def test_invalid_value_stops_startup_before_placeholder(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CRICKEY_PORT", "0")

    assert main(["serve"]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert (
        captured.err == "ERROR: CRICKEY_PORT='0' is invalid; expected an integer from 1 to 65535.\n"
    )


def test_min_interval_floor() -> None:
    assert _settings_from_cli(["serve", "--min-interval", "2s"]).min_interval == timedelta(
        seconds=2
    )

    with pytest.raises(SettingsError) as exc_info:
        _settings_from_cli(["serve", "--min-interval", "1.999s"])

    assert (
        str(exc_info.value)
        == "--min-interval='1.999s' is invalid; expected a duration of at least 2s."
    )


def test_min_interval_warning_below_fifteen_seconds(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="crickey.settings"):
        settings = _settings_from_cli(["serve", "--min-interval", "14s"])

    assert settings.min_interval == timedelta(seconds=14)
    assert caplog.messages == ["CRICKEY_MIN_INTERVAL is below D9's 15s default spacing: 14s"]


def test_min_interval_no_warning_at_fifteen_seconds(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="crickey.settings"):
        settings = _settings_from_cli(["serve", "--min-interval", "15s"])

    assert settings.min_interval == timedelta(seconds=15)
    assert caplog.messages == []


@pytest.mark.parametrize(
    "bad_value",
    ["0", "0s", "-1s", "1d", "", " ", " 5m", "5m "],
)
def test_duration_parsing_edge_cases_are_refused(
    bad_value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_crickey_env(monkeypatch)
    monkeypatch.setenv("CRICKEY_RECENT_TTL", bad_value)

    with pytest.raises(SettingsError) as exc_info:
        load_settings(_empty_args())

    assert str(exc_info.value).startswith(f"CRICKEY_RECENT_TTL={bad_value!r} is invalid; expected ")


def test_duration_suffixes(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_crickey_env(monkeypatch)
    monkeypatch.setenv("CRICKEY_RECENT_TTL", "15")
    assert load_settings(_empty_args()).recent_ttl == timedelta(seconds=15)
    monkeypatch.setenv("CRICKEY_RECENT_TTL", "90s")
    assert load_settings(_empty_args()).recent_ttl == timedelta(seconds=90)
    monkeypatch.setenv("CRICKEY_RECENT_TTL", "5m")
    assert load_settings(_empty_args()).recent_ttl == timedelta(minutes=5)
    monkeypatch.setenv("CRICKEY_RECENT_TTL", "1h")
    assert load_settings(_empty_args()).recent_ttl == timedelta(hours=1)


def test_block_pause_list_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_crickey_env(monkeypatch)
    monkeypatch.setenv("CRICKEY_BLOCK_PAUSES", "5m, 15m,1h")

    settings = load_settings(_empty_args())

    assert settings.block_pauses == (
        timedelta(minutes=5),
        timedelta(minutes=15),
        timedelta(hours=1),
    )


@pytest.mark.parametrize("bad_value", ["", ",", "5m,", ",5m", "0s", "5m,0s"])
def test_block_pause_list_invalid_values(
    bad_value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_crickey_env(monkeypatch)
    monkeypatch.setenv("CRICKEY_BLOCK_PAUSES", bad_value)

    with pytest.raises(SettingsError) as exc_info:
        load_settings(_empty_args())

    assert str(exc_info.value).startswith(
        f"CRICKEY_BLOCK_PAUSES={bad_value!r} is invalid; expected "
    )


def test_port_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_crickey_env(monkeypatch)
    monkeypatch.setenv("CRICKEY_PORT", "1")
    assert load_settings(_empty_args()).port == 1
    monkeypatch.setenv("CRICKEY_PORT", "65535")
    assert load_settings(_empty_args()).port == 65535

    for bad_value in ("0", "65536"):
        monkeypatch.setenv("CRICKEY_PORT", bad_value)
        with pytest.raises(SettingsError) as exc_info:
            load_settings(_empty_args())
        assert str(exc_info.value).startswith(f"CRICKEY_PORT={bad_value!r} is invalid; expected ")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", False),
        ("1", True),
        ("0", False),
        ("true", True),
        ("false", False),
        ("TRUE", True),
        ("FALSE", False),
        ("yes", True),
        ("no", False),
        ("YES", True),
        ("NO", False),
    ],
)
def test_boolean_parsing(raw: str, expected: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_crickey_env(monkeypatch)
    monkeypatch.setenv("CRICKEY_IN_CONTAINER", raw)

    assert load_settings(_empty_args()).in_container is expected


def test_settings_are_immutable() -> None:
    settings = Settings()

    with pytest.raises(FrozenInstanceError):
        settings.port = 9000
