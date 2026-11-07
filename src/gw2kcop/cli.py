"""Command-line interface.

Commands that are not implemented yet exit with code 2 and a clear message
instead of doing anything partial.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from gw2kcop import __version__
from gw2kcop.accounts import AccountsError, load_accounts
from gw2kcop.config import ConfigError, load_config, resolve_config_path, write_template
from gw2kcop.prepare import prepare

OK = 0
ERROR = 1
NOT_IMPLEMENTED_EXIT = 2

NOT_IMPLEMENTED: dict[str, str] = {
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
    parser.add_argument("--config", metavar="PATH", help="configuration file (default: see 'gw2kcop config path')")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    cfg = sub.add_parser("config", help="create or show the configuration file")
    cfg.add_argument("action", choices=["init", "show", "path"])

    prep = sub.add_parser(
        "prepare",
        help="create export folders for a month: <exports>/RRRR-MM/<account_id>/",
        description="Create one folder per active account for a month, or --start for full histories.",
    )
    target = prep.add_mutually_exclusive_group(required=True)
    target.add_argument("month", nargs="?", help="month as RRRR-MM, e.g. 2026-11")
    target.add_argument("--start", action="store_true", help="create _start/<account_id>/ for full histories")

    for name, help_text in NOT_IMPLEMENTED.items():
        sub.add_parser(name, help=help_text, description=help_text)
    return parser


def _cmd_config(args: argparse.Namespace) -> int:
    path = resolve_config_path(args.config)
    if args.action == "path":
        print(path)
        return OK
    if args.action == "init":
        if write_template(path):
            print(f"Created {path}. Check the paths in it before running other commands.")
        else:
            print(f"Already exists, not changed: {path}")
        return OK
    config = load_config(args.config)
    print(f"config:    {config.source}")
    print(f"timezone:  {config.timezone.key}")
    print(f"exports:   {config.exports_dir}")
    print(f"data:      {config.data_dir}")
    print(f"accounts:  {config.accounts_file}")
    return OK


def _cmd_prepare(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    accounts = load_accounts(config.accounts_file)
    result = prepare(config.exports_dir, accounts, None if args.start else args.month, start=args.start)
    print(f"{result.root}: {len(result.created)} folder(s) created, {len(result.existing)} already there.")
    for path in result.created:
        print(f"  + {path.name}")
    return OK


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return OK
    try:
        if args.command == "config":
            return _cmd_config(args)
        if args.command == "prepare":
            return _cmd_prepare(args)
    except (ConfigError, AccountsError, ValueError) as exc:
        print(f"gw2kcop {args.command}: {exc}", file=sys.stderr)
        return ERROR
    print(f"gw2kcop {args.command}: not implemented yet.", file=sys.stderr)
    return NOT_IMPLEMENTED_EXIT


if __name__ == "__main__":
    raise SystemExit(main())
