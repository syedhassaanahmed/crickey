from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence

from crickey.settings import SettingsError, add_settings_flags, load_settings

LOGGER = logging.getLogger("crickey")


def _serve(args: argparse.Namespace) -> int:
    load_settings(args)
    print(
        "crickey serve is a placeholder; HTTP transport will be implemented in issue #10.",
        file=sys.stderr,
    )
    return 1


def _stdio(args: argparse.Namespace) -> int:
    load_settings(args)
    print(
        "crickey stdio is a placeholder; the stdio transport will be implemented in issue #10.",
        file=sys.stderr,
    )
    return 1


def build_parser() -> argparse.ArgumentParser:
    settings_parent = argparse.ArgumentParser(add_help=False)
    add_settings_flags(settings_parent)

    parser = argparse.ArgumentParser(prog="crickey", parents=[settings_parent])
    parser.set_defaults(func=_serve)
    subparsers = parser.add_subparsers(dest="command")

    serve = subparsers.add_parser(
        "serve",
        help="serve over Streamable HTTP (placeholder)",
        parents=[settings_parent],
    )
    serve.set_defaults(func=_serve)

    stdio = subparsers.add_parser(
        "stdio",
        help="serve over stdio for debugging (placeholder)",
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
    except SettingsError as error:
        LOGGER.error("%s", error)
        return 2
    finally:
        LOGGER.removeHandler(handler)
