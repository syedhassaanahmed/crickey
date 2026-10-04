from __future__ import annotations

import argparse
import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta

LOGGER = logging.getLogger(__name__)

_DURATION_RE = re.compile(r"(?P<number>(?:\d+(?:\.\d+)?)|(?:\.\d+))(?P<unit>[smh]?)")
_MAX_DURATION = timedelta(hours=168)
_DURATION_EXPECTED = (
    "a positive duration in seconds, or a number with an s, m or h suffix "
    "(examples: 90, 5m, 1h), no more than 168h"
)
_MIN_INTERVAL_EXPECTED = (
    "a positive duration in seconds, or a number with an s, m or h suffix "
    "(examples: 90, 5m, 1h), from 2s through 168h"
)
_BLOCK_PAUSES_EXPECTED = (
    "a comma-separated list of positive durations in seconds, or numbers with an s, m or h suffix "
    "(examples: 90, 5m, 1h), each no more than 168h"
)


class SettingsError(ValueError):
    """Raised when a setting cannot be loaded."""


@dataclass(frozen=True)
class Settings:
    min_interval: timedelta = timedelta(seconds=15)
    max_retries: int = 3
    block_pauses: tuple[timedelta, ...] = (
        timedelta(minutes=5),
        timedelta(minutes=15),
        timedelta(hours=1),
        timedelta(hours=4),
        timedelta(hours=24),
    )
    max_pages: int = 4
    cache_max_mb: int = 64
    recent_ttl: timedelta = timedelta(hours=1)
    port: int = 8765
    in_container: bool = False


@dataclass(frozen=True)
class _SettingSpec:
    field_name: str
    env_name: str
    flag_name: str
    expected: str

    @property
    def dest(self) -> str:
        return self.flag_name.removeprefix("--").replace("-", "_")


_SPECS: tuple[_SettingSpec, ...] = (
    _SettingSpec("min_interval", "CRICKEY_MIN_INTERVAL", "--min-interval", _MIN_INTERVAL_EXPECTED),
    _SettingSpec("max_retries", "CRICKEY_MAX_RETRIES", "--max-retries", "a non-negative integer"),
    _SettingSpec(
        "block_pauses",
        "CRICKEY_BLOCK_PAUSES",
        "--block-pauses",
        _BLOCK_PAUSES_EXPECTED,
    ),
    _SettingSpec("max_pages", "CRICKEY_MAX_PAGES", "--max-pages", "a positive integer"),
    _SettingSpec("cache_max_mb", "CRICKEY_CACHE_MAX_MB", "--cache-max-mb", "a positive integer"),
    _SettingSpec("recent_ttl", "CRICKEY_RECENT_TTL", "--recent-ttl", _DURATION_EXPECTED),
    _SettingSpec("port", "CRICKEY_PORT", "--port", "an integer from 1 to 65535"),
    _SettingSpec(
        "in_container",
        "CRICKEY_IN_CONTAINER",
        "--in-container",
        "1/0, true/false, yes/no, or empty",
    ),
)

_SPECS_BY_FIELD = {spec.field_name: spec for spec in _SPECS}


def add_settings_flags(parser: argparse.ArgumentParser) -> None:
    # SUPPRESS stops a subcommand from resetting a flag given before it
    # (`crickey --port 9000 serve`).
    for spec in _SPECS:
        parser.add_argument(
            spec.flag_name, dest=spec.dest, metavar="VALUE", default=argparse.SUPPRESS
        )


def load_settings(
    args: argparse.Namespace,
    environ: Mapping[str, str] | None = None,
) -> Settings:
    environ = os.environ if environ is None else environ
    values: dict[str, object] = {}
    for spec in _SPECS:
        raw = getattr(args, spec.dest, None)
        source = spec.flag_name
        if raw is None:
            raw = environ.get(spec.env_name)
            source = spec.env_name
        if raw is None:
            continue
        values[spec.field_name] = _parse_value(spec, raw, source)

    settings = Settings(**values)
    if settings.min_interval < timedelta(seconds=2):
        spec = _SPECS_BY_FIELD["min_interval"]
        source, raw = _setting_source(args, environ, spec)
        raise SettingsError(_invalid_message(source, raw, spec.expected))
    if settings.min_interval < timedelta(seconds=15):
        spec = _SPECS_BY_FIELD["min_interval"]
        source, raw = _setting_source(args, environ, spec)
        LOGGER.warning(
            "%s=%s is below 15s, Cricinfo's crawl delay; requesting faster risks being blocked.",
            source,
            raw,
        )
    return settings


def _setting_source(
    args: argparse.Namespace,
    environ: Mapping[str, str],
    spec: _SettingSpec,
) -> tuple[str, str]:
    raw = getattr(args, spec.dest, None)
    if raw is not None:
        return spec.flag_name, raw
    return spec.env_name, environ.get(spec.env_name, "")


def _parse_value(spec: _SettingSpec, raw: str, source: str) -> object:
    try:
        if spec.field_name in {"min_interval", "recent_ttl"}:
            return _parse_duration(raw)
        if spec.field_name == "block_pauses":
            return _parse_block_pauses(raw)
        if spec.field_name == "max_retries":
            return _parse_non_negative_int(raw)
        if spec.field_name in {"max_pages", "cache_max_mb"}:
            return _parse_positive_int(raw)
        if spec.field_name == "port":
            port = _parse_positive_int(raw)
            if port > 65535:
                raise ValueError
            return port
        if spec.field_name == "in_container":
            return _parse_bool(raw)
    except ValueError as error:
        raise SettingsError(_invalid_message(source, raw, spec.expected)) from error
    raise AssertionError(f"unhandled setting {spec.field_name}")


def _parse_duration(raw: str) -> timedelta:
    if raw != raw.strip():
        raise ValueError
    match = _DURATION_RE.fullmatch(raw)
    if match is None:
        raise ValueError
    number = float(match.group("number"))
    if number <= 0:
        raise ValueError
    unit = match.group("unit")
    multiplier = {"": 1, "s": 1, "m": 60, "h": 3600}[unit]
    seconds = number * multiplier
    if seconds > _MAX_DURATION.total_seconds():
        raise ValueError
    return timedelta(seconds=seconds)


def _parse_block_pauses(raw: str) -> tuple[timedelta, ...]:
    parts = raw.split(",")
    if not parts:
        raise ValueError
    pauses = tuple(_parse_duration(part.strip()) for part in parts)
    if not pauses:
        raise ValueError
    return pauses


def _parse_non_negative_int(raw: str) -> int:
    if raw != raw.strip() or not raw.isdecimal():
        raise ValueError
    return int(raw)


def _parse_positive_int(raw: str) -> int:
    value = _parse_non_negative_int(raw)
    if value <= 0:
        raise ValueError
    return value


def _parse_bool(raw: str) -> bool:
    if raw == "":
        return False
    normalized = raw.lower()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no"}:
        return False
    raise ValueError


def _invalid_message(source: str, raw: str, expected: str) -> str:
    return f"{source}={raw!r} is invalid; expected {expected}."


def _format_duration(duration: timedelta) -> str:
    seconds = duration.total_seconds()
    if seconds.is_integer():
        return f"{int(seconds)}s"
    return f"{seconds:g}s"
