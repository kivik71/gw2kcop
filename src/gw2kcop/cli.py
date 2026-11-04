"""Command-line interface.

Subcommands are placeholders until their stage is implemented. Each one exits
with code 2 and a clear message instead of doing anything partial.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from gw2kcop import __version__

NOT_IMPLEMENTED_EXIT = 2

SUBCOMMANDS: dict[str, str] = {
    "prepare": "create export folders for a month: <exports>/RRRR-MM/<account_id>/",
    "transactions": "parse exports and merge new rows into transactions.csv",
    "fx": "download NBP exchange rates into fx_rates.csv",
    "prices": "download daily closing prices into prices.csv",
    "anonymize": "make an anonymized sample from a real export",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gw2kcop",
        description="Parsers for (Polish) bank and broker exports to a common transactions CSV.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    for name, help_text in SUBCOMMANDS.items():
        sub.add_parser(name, help=help_text, description=help_text)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    print(f"gw2kcop {args.command}: not implemented yet.", file=sys.stderr)
    return NOT_IMPLEMENTED_EXIT


if __name__ == "__main__":
    raise SystemExit(main())
