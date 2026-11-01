from __future__ import annotations

import ipaddress
import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from urllib.parse import urlsplit

LOGGER = logging.getLogger(__name__)

_DURATION_RE = re.compile(r"(?P<number>(?:\d+(?:\.\d+)?)|(?:\.\d+))(?P<unit>[smh]?)")
_MAX_DURATION = timedelta(hours=168)
_MIN_INTERVAL_EXPECTED = (
    "a positive duration in seconds, or a number with an s, m or h suffix "
    "(examples: 90, 5m, 1h), from 2s through 168h"
)
# An instance's URL: a host name or an IPv6 address in brackets, with an optional port.
_URL_RE = re.compile(r"https?://(?:[a-z0-9-]+(?:\.[a-z0-9-]+)*|\[[0-9a-f:.]+\])(?::\d{1,5})?/?")
_DEFAULT_PORTS = {"http": 80, "https": 443}
TOKEN_MIN_LENGTH = 32
_TOKEN_EXPECTED = f"at least {TOKEN_MIN_LENGTH} characters, with no spaces"
LOCAL_HOSTS = ("127.0.0.1:*", "localhost:*", "[::1]:*")
LOCAL_ORIGINS = ("http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*")


class SettingsError(ValueError):
    """Raised when a setting cannot be loaded."""


@dataclass(frozen=True)
class Settings:
    min_interval: timedelta = timedelta(seconds=15)
    # No environment variable sets these five: crickey's limits on retries, pauses, pages and
    # freshness keep it polite, so they're fixed; tests pass smaller values.
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
    # None binds 127.0.0.1 natively and 0.0.0.0 in a container, whose port Docker publishes on
    # the host's loopback.
    host: str | None = None
    # The instance's public URLs; the Host and Origin checks accept them as well as localhost.
    urls: tuple[str, ...] = ()
    # A secret, so it stays out of repr and error messages.
    token: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class _SettingSpec:
    field_name: str
    env_name: str
    expected: str


_SPECS: tuple[_SettingSpec, ...] = (
    _SettingSpec("min_interval", "CRICKEY_MIN_INTERVAL", _MIN_INTERVAL_EXPECTED),
    _SettingSpec("port", "CRICKEY_PORT", "an integer from 1 to 65535"),
    _SettingSpec("in_container", "CRICKEY_IN_CONTAINER", "1/0, true/false, yes/no, or empty"),
    _SettingSpec("host", "CRICKEY_HOST", "an IP address, such as 127.0.0.1 or 0.0.0.0"),
    _SettingSpec(
        "urls",
        "CRICKEY_URL",
        "a comma-separated list of http:// or https:// URLs without a path, "
        "such as https://crickey.example.com",
    ),
)
_TOKEN_ENV = "CRICKEY_TOKEN"
_MIN_INTERVAL_ENV = "CRICKEY_MIN_INTERVAL"


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Settings from the environment; anything unset keeps its default."""
    environ = os.environ if environ is None else environ
    values: dict[str, object] = {}
    for spec in _SPECS:
        raw = environ.get(spec.env_name)
        if raw is not None:
            values[spec.field_name] = _parse_value(spec, raw)
    token = environ.get(_TOKEN_ENV)
    if token:
        if len(token) < TOKEN_MIN_LENGTH or not all("!" <= char <= "~" for char in token):
            raise SettingsError(f"{_TOKEN_ENV} is invalid; expected {_TOKEN_EXPECTED}.")
        values["token"] = token

    settings = Settings(**values)
    if settings.min_interval < timedelta(seconds=2):
        raw = environ.get(_MIN_INTERVAL_ENV, "")
        raise SettingsError(_invalid_message(_MIN_INTERVAL_ENV, raw, _MIN_INTERVAL_EXPECTED))
    if settings.min_interval < timedelta(seconds=15):
        LOGGER.warning(
            "%s=%s is below 15s, Cricinfo's crawl delay; requesting faster risks being blocked.",
            _MIN_INTERVAL_ENV,
            environ.get(_MIN_INTERVAL_ENV, ""),
        )
    return settings


def _parse_value(spec: _SettingSpec, raw: str) -> object:
    try:
        if spec.field_name == "min_interval":
            return _parse_duration(raw)
        if spec.field_name == "port":
            port = _parse_positive_int(raw)
            if port > 65535:
                raise ValueError
            return port
        if spec.field_name == "in_container":
            return _parse_bool(raw)
        if spec.field_name == "host":
            return str(ipaddress.ip_address(raw))
        if spec.field_name == "urls":
            return _parse_urls(raw)
    except ValueError as error:
        raise SettingsError(_invalid_message(spec.env_name, raw, spec.expected)) from error
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


def _parse_non_negative_int(raw: str) -> int:
    if raw != raw.strip() or not raw.isdecimal():
        raise ValueError
    return int(raw)


def _parse_positive_int(raw: str) -> int:
    value = _parse_non_negative_int(raw)
    if value <= 0:
        raise ValueError
    return value


def _parse_urls(raw: str) -> tuple[str, ...]:
    """Each URL as its origin, the form browsers send: lower case, without a default port."""
    urls = []
    for item in raw.split(","):
        url = item.strip().lower()
        if not _URL_RE.fullmatch(url):
            raise ValueError
        parts = urlsplit(url)
        host = f"[{parts.hostname}]" if ":" in (parts.hostname or "") else parts.hostname
        port = parts.port  # Raises ValueError above 65535.
        default = port is None or port == _DEFAULT_PORTS[parts.scheme]
        urls.append(f"{parts.scheme}://{host}" + ("" if default else f":{port}"))
    return tuple(urls)


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
