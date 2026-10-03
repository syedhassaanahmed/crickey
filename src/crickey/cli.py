from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence


def _serve(_args: argparse.Namespace) -> int:
    print(
        "crickey serve is a placeholder; HTTP transport will be implemented in issue #10.",
        file=sys.stderr,
    )
    return 1


def _stdio(_args: argparse.Namespace) -> int:
    print(
        "crickey stdio is a placeholder; the stdio transport will be implemented in issue #10.",
        file=sys.stderr,
    )
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="crickey")
    parser.set_defaults(func=_serve)
    subparsers = parser.add_subparsers(dest="command")

    serve = subparsers.add_parser("serve", help="serve over Streamable HTTP (placeholder)")
    serve.set_defaults(func=_serve)

    stdio = subparsers.add_parser("stdio", help="serve over stdio for debugging (placeholder)")
    stdio.set_defaults(func=_stdio)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
