from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence

from crickey.server import create_server
from crickey.settings import SettingsError, add_settings_flags, load_settings
from crickey.transport import TransportError, run_stdio, serve_http

LOGGER = logging.getLogger("crickey")
LOGGER.propagate = False


def _serve(args: argparse.Namespace) -> int:
    settings = load_settings(args)
    serve_http(create_server(settings), settings)
    return 0


def _stdio(args: argparse.Namespace) -> int:
    settings = load_settings(args)
    run_stdio(create_server(settings))
    return 0


def build_parser() -> argparse.ArgumentParser:
    settings_parent = argparse.ArgumentParser(add_help=False)
    add_settings_flags(settings_parent)

    parser = argparse.ArgumentParser(prog="crickey", parents=[settings_parent])
    parser.set_defaults(func=_serve)
    subparsers = parser.add_subparsers(dest="command")

    serve = subparsers.add_parser(
        "serve",
        help="serve over Streamable HTTP",
        parents=[settings_parent],
    )
    serve.set_defaults(func=_serve)

    stdio = subparsers.add_parser(
        "stdio",
        help="serve over stdio for debugging",
        parents=[settings_parent],
    )
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
