from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence

from crickey.server import create_server
from crickey.settings import SettingsError, load_settings
from crickey.transport import TransportError, run_stdio, serve_http

LOGGER = logging.getLogger("crickey")
LOGGER.propagate = False


def _serve(_args: argparse.Namespace) -> int:
    settings = load_settings()
    serve_http(create_server(settings), settings)
    return 0


def _stdio(_args: argparse.Namespace) -> int:
    settings = load_settings()
    run_stdio(create_server(settings))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crickey", epilog="Settings come from CRICKEY_* environment variables; see the README."
    )
    parser.set_defaults(func=_serve)
    subparsers = parser.add_subparsers(dest="command")

    serve = subparsers.add_parser("serve", help="serve over Streamable HTTP")
    serve.set_defaults(func=_serve)

    stdio = subparsers.add_parser("stdio", help="serve over stdio for debugging")
    stdio.set_defaults(func=_stdio)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.WARNING)
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        return args.func(args)
    except (SettingsError, TransportError) as error:
        LOGGER.error("%s", error)
        return 2 if isinstance(error, SettingsError) else 1
    finally:
        LOGGER.removeHandler(handler)
