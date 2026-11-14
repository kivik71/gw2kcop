"""Command-line interface.

Commands that are not implemented yet exit with code 2 and a clear message
instead of doing anything partial.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from pathlib import Path

from gw2kcop import __version__
from gw2kcop.accounts import AccountsError, load_accounts
from gw2kcop.config import ConfigError, load_config, resolve_config_path, write_template
from gw2kcop.prepare import prepare

OK = 0
ERROR = 1
NOT_IMPLEMENTED_EXIT = 2

NOT_IMPLEMENTED: dict[str, str] = {
    "prices": "download daily closing prices into prices.csv",
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

    anon = sub.add_parser(
        "anonymize",
        help="make an anonymized sample from a real export (CSV)",
        description="Make an anonymized copy of an export (file or folder of .csv files), "
        "or --check a finished sample.",
    )
    anon.add_argument("path", help="export file, folder of exports, or the sample to --check")
    anon.add_argument("--check", action="store_true", help="only scan PATH for identifiers left behind")
    anon.add_argument("--output", metavar="DIR", help="output folder (default: paths.samples)")
    anon.add_argument("--rows", type=int, metavar="N", help="keep at most N data rows (varied types)")
    anon.add_argument("--keep-last4", action="store_true", help="keep the last 4 digits of account numbers")
    anon.add_argument("--decimal", choices=[",", "."], help="decimal separator (default: detected)")
    anon.add_argument("--scale-quantities", action="store_true", help="also scale quantity columns")
    anon.add_argument("--mapping-dir", metavar="DIR", help="anonymizer folder (default: paths.anonymizer)")

    tx = sub.add_parser(
        "transactions",
        help="import exports into transactions.csv",
        description="Import every export of a month folder (<exports>/RRRR-MM/<account_id>/) "
        "into <data>/transactions.csv. Files that import cleanly move to _zaimportowane/.",
    )
    tx_sub = tx.add_subparsers(dest="action", metavar="ACTION", required=True)
    imp = tx_sub.add_parser("import", help="import a month folder (or --start)")
    which = imp.add_mutually_exclusive_group(required=True)
    which.add_argument("month", nargs="?", help="month as RRRR-MM, e.g. 2026-11")
    which.add_argument("--start", action="store_true", help="import _start/ (full histories)")
    imp.add_argument("--dry-run", action="store_true", help="parse and check only; write and move nothing")
    imp.add_argument("--account", metavar="ID", help="only this account_id")
    opening = tx_sub.add_parser(
        "opening", help="add manual opening balances from <data>/opening_balances.csv",
        description="Add positions and cash held before the known history (rows of "
        "<data>/opening_balances.csv) to transactions.csv as type opening_balance.")
    opening.add_argument("--dry-run", action="store_true", help="check only; write nothing")

    fx = sub.add_parser(
        "fx", help="download NBP exchange rates into fx_rates.csv",
        description="Download NBP table A average rates (api.nbp.pl) into <data>/fx_rates.csv. "
        "By default: every foreign currency in transactions.csv, from the day after the last "
        "known rate (or a week before the first transaction) to today.")
    fx.add_argument("--currencies", metavar="USD,EUR", help="only these currency codes")
    fx.add_argument("--from", dest="start", metavar="YYYY-MM-DD", help="first day (default: see above)")
    fx.add_argument("--to", dest="end", metavar="YYYY-MM-DD", help="last day (default: today)")
    fx.add_argument("--dry-run", action="store_true", help="download and check only; write nothing")

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
    print(f"config:     {config.source}")
    print(f"timezone:   {config.timezone.key}")
    print(f"exports:    {config.exports_dir}")
    print(f"data:       {config.data_dir}")
    print(f"accounts:   {config.accounts_file}")
    print(f"anonymizer: {config.anonymizer_dir or '(not set)'}")
    print(f"samples:    {config.samples_dir or '(not set)'}")
    return OK


def _cmd_prepare(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    accounts = load_accounts(config.accounts_file)
    result = prepare(config.exports_dir, accounts, None if args.start else args.month, start=args.start)
    print(f"{result.root}: {len(result.created)} folder(s) created, {len(result.existing)} already there.")
    for path in result.created:
        print(f"  + {path.name}")
    return OK


def _csv_files(source: Path) -> list[Path]:
    """CSV files in a folder, any letter case (mBank eMakler writes ``.Csv``)."""
    if not source.is_dir():
        return [source]
    return sorted(p for p in source.iterdir() if p.is_file() and p.suffix.lower() == ".csv")


def _require_exists(path: Path) -> None:
    """Stop with a readable message (and a hint for imported files) if ``path`` is missing."""
    if path.exists():
        return
    message = f"not found: {path}"
    moved = path.parent / "_zaimportowane" / path.name
    if moved.is_file():
        message += f"\n  it was imported and moved to: {moved}"
    raise ValueError(message)


def _anonymizer_dir(args: argparse.Namespace, config) -> Path:
    if args.mapping_dir:
        return Path(args.mapping_dir).expanduser()
    if config and config.anonymizer_dir:
        return config.anonymizer_dir
    raise ConfigError("set paths.anonymizer in the config file or pass --mapping-dir")


def _cmd_anonymize(args: argparse.Namespace) -> int:
    from gw2kcop.anonymize.core import Options, anonymize_file, check_text
    from gw2kcop.anonymize.mapping import load_mapping, load_terms, save_mapping
    from gw2kcop.io.text import read_text

    try:
        config = load_config(args.config)
    except ConfigError:
        if not args.mapping_dir:
            raise
        config = None
    folder = _anonymizer_dir(args, config)
    mapping = load_mapping(folder)
    source = Path(args.path).expanduser()
    _require_exists(source)

    if args.check:
        samples = _csv_files(source)
        if not samples:
            raise ValueError(f"no .csv files in {source}")
        status = OK
        for sample in samples:
            findings = check_text(read_text(sample).text, mapping)
            for finding in findings:
                print(f"  ! {finding}")
            print(f"{sample.name}: {'NOT safe to share' if findings else 'no identifiers found'} "
                  f"({len(findings)} finding(s)).")
            if findings:
                status = ERROR
        print("Always also read the samples yourself.")
        return status

    terms = load_terms(folder)
    if config and config.accounts_file.is_file():
        terms += [a.export_identifier for a in load_accounts(config.accounts_file) if a.export_identifier]
        terms = sorted(set(terms), key=len, reverse=True)
    if args.output:
        output_dir = Path(args.output).expanduser()
    elif config and config.samples_dir:
        output_dir = config.samples_dir
    else:
        raise ConfigError("set paths.samples in the config file or pass --output")
    files = _csv_files(source)
    skipped = sorted(p.name for p in source.iterdir() if p.is_file() and p.suffix.lower() != ".csv") if source.is_dir() else []
    if not files:
        raise ValueError(f"no .csv files in {source}")
    options = Options(rows=args.rows, keep_last=4 if args.keep_last4 else 0,
                      decimal_sep=args.decimal, scale_quantities=args.scale_quantities)
    status = OK
    for path in files:
        if path.suffix.lower() != ".csv":
            raise ValueError(f"{path.name}: only .csv is supported for now (XLSX/XLS: next stage)")
        report = anonymize_file(path, output_dir, mapping, terms, options)
        save_mapping(folder, mapping)
        print(f"{path.name} -> {report.output}")
        print(f"  encoding {report.encoding}, delimiter {report.delimiter!r}, decimal {report.decimal_sep!r}, "
              f"rows {report.data_rows_out}/{report.data_rows_in}")
        print(f"  scaled columns: {', '.join(report.scaled_columns) or '-'}")
        if report.kept_quantity_columns:
            print(f"  quantity columns kept: {', '.join(report.kept_quantity_columns)}")
        print(f"  replaced: {dict(report.replaced) or '-'}")
        for note in report.suspicious:
            print(f"  ? {note}")
        findings = check_text(read_text(report.output).text, mapping)
        for finding in findings:
            print(f"  ! {finding}")
        if findings:
            status = ERROR
    for name in skipped:
        print(f"skipped (not .csv yet): {name}")
    print("Check every sample by eye before sharing it. '?' lines need your attention.")
    return status


def _mask(text: str) -> str:
    """Long digit runs are account numbers (XTB file names): never print them."""
    return re.sub(r"[0-9]{6,}", "…", text)


def _cmd_fx(args: argparse.Namespace) -> int:
    from datetime import date, datetime, timedelta

    from gw2kcop.fx import nbp
    from gw2kcop.fx import rates as fx

    config = load_config(args.config)
    path = config.data_dir / fx.FILE_NAME
    try:
        start = date.fromisoformat(args.start) if args.start else None
        end = date.fromisoformat(args.end) if args.end else datetime.now(config.timezone).date()
    except ValueError:
        raise ValueError("dates must be YYYY-MM-DD") from None
    base = nbp.BASE_CURRENCY  # the only source so far
    codes = sorted({c.strip().upper() for c in args.currencies.split(",") if c.strip()}) if args.currencies \
        else sorted(fx.wanted_currencies(config.data_dir, base))
    if not codes:
        print(f"No currency other than {base} in transactions.csv; nothing to download (use --currencies).")
        return OK
    known = fx.last_known(path, base)
    first = fx.first_transaction_date(config.data_dir)
    found = []
    for code in codes:
        begin = start or (known[code] + timedelta(days=1) if code in known
                          else (first - timedelta(days=7) if first else end - timedelta(days=7)))
        got = nbp.download(code, begin, end) if begin <= end else []
        print(f"  {code}/{base} ({nbp.SOURCE}): {len(got)} rate(s) {begin} .. {end}")
        found.extend(got)
    added, already = fx.merge(path, found, dry_run=args.dry_run)
    verb = "would add" if args.dry_run else "added"
    print(f"{fx.FILE_NAME}: {verb} {added}, already known {already}")
    if args.dry_run:
        print("Dry run: nothing was written.")
    return OK


def _cmd_opening(args: argparse.Namespace) -> int:
    from gw2kcop.transactions import master
    from gw2kcop.transactions.opening import FILE_NAME, OpeningError, read_opening
    from gw2kcop.transactions.reference import load_reference

    config = load_config(args.config)
    accounts = load_accounts(config.accounts_file)
    path = config.data_dir / FILE_NAME
    _require_exists(path)
    try:
        txs = read_opening(path, accounts, load_reference(config.data_dir))
    except OpeningError as exc:
        print(f"! {FILE_NAME}: NOT imported")
        for line in exc.errors:
            print(f"    {line}")
        return ERROR
    if args.dry_run:
        added, known = master.count_new(config.data_dir, txs)
        print(f"  {FILE_NAME}: would add {added}, already known {known} (opening_balance)")
        print("Dry run: nothing was written.")
        return OK
    result = master.merge(config.data_dir, txs, FILE_NAME)
    print(f"  {FILE_NAME}: added {result.added}, already known {result.skipped} (opening_balance)")
    return OK


def _cmd_transactions(args: argparse.Namespace) -> int:
    if args.action == "opening":
        return _cmd_opening(args)
    from gw2kcop.transactions.formats import builtin_specs
    from gw2kcop.transactions.importer import import_folder
    from gw2kcop.transactions.reference import load_reference

    config = load_config(args.config)
    accounts = load_accounts(config.accounts_file)
    reference = load_reference(config.data_dir)
    results, notes = import_folder(config, accounts, builtin_specs(), reference,
                                   None if args.start else args.month, dry_run=args.dry_run,
                                   only_account=args.account)
    status = OK
    missing: dict[str, set[str]] = {}
    for r in results:
        shown = f"{r.path.name}:{r.member.rsplit('/', 1)[-1]}" if r.member else r.path.name
        name = f"{r.account_id}/{_mask(shown)}"
        if r.status == "error":
            status = ERROR
            print(f"! {name}: NOT imported ({r.format or 'unknown type'})")
            for line in r.message.splitlines():
                print(f"    {line}")
            if r.missing_aliases:
                institution = next((a.institution for a in accounts if a.account_id == r.account_id), "?")
                missing.setdefault(institution, set()).update(r.missing_aliases)
                print(f"    unknown instruments: {', '.join(r.missing_aliases)}")
        else:
            verb = "would add" if r.status == "checked" else "added"
            target = "" if r.target == "transactions.csv" else f" -> {r.target}"
            print(f"  {name}: {verb} {r.added}, already known {r.skipped} ({r.format}{target})")
            if r.note:
                print(f"    {r.note}")
    for note in notes:
        print(f"  - {_mask(note)}")
    if missing:
        print("\nAdd these lines to instrument_aliases.csv (institution,alias,ticker), "
              "with the ticker from instruments.csv:")
        for institution, aliases in sorted(missing.items()):
            for alias in sorted(aliases):
                print(f"  {institution},{alias},<ticker>")
    if not results:
        print("No export files found to import.")
    elif args.dry_run:
        print("Dry run: nothing was written or moved.")
    return status


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
        if args.command == "anonymize":
            return _cmd_anonymize(args)
        if args.command == "transactions":
            return _cmd_transactions(args)
        if args.command == "fx":
            return _cmd_fx(args)
    except (ConfigError, AccountsError, ValueError) as exc:
        print(f"gw2kcop {args.command}: {exc}", file=sys.stderr)
        return ERROR
    except OSError as exc:  # missing file, no permission, ...: no traceback for the user
        print(f"gw2kcop {args.command}: {exc.strerror or exc}: {exc.filename or ''}".rstrip(": "),
              file=sys.stderr)
        return ERROR
    print(f"gw2kcop {args.command}: not implemented yet.", file=sys.stderr)
    return NOT_IMPLEMENTED_EXIT


if __name__ == "__main__":
    raise SystemExit(main())
