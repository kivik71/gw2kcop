# gw2kcop
(gw 2k COP)

**Parsers for (Polish) bank and broker exports to a common transactions CSV**

> Status: early development. `config`, `prepare`, `anonymize` (CSV) and `transactions import` (mBank brokerage) work; the other commands are not implemented yet.

## What it does
`gw2kcop` reads the files you download from your bank or broker and turns them into one
consistent `transactions.csv`, plus daily FX rates (NBP) and closing prices.
Everything runs **locally**. The tool never logs in to a bank, never scrapes websites and
never stores passwords.

| Command | Purpose |
|---|---|
| `gw2kcop config init` | create the configuration file (paths, time zone) |
| `gw2kcop prepare RRRR-MM` | create export folders for a month: `RRRR-MM/<account_id>/` (`--start` for full histories) |
| `gw2kcop transactions import RRRR-MM` | parse a month's exports, deduplicate, append new rows to `transactions.csv` (`--dry-run` to check only) |
| `gw2kcop fx` | download NBP exchange rates |
| `gw2kcop prices` | download daily closing prices (optional Yahoo adapter) |
| `gw2kcop anonymize PATH` | make an anonymized sample from a real CSV export (`--check` to scan a sample) |

## Quick start
```
gw2kcop config init      # creates the config file; prints where it is
gw2kcop config show      # check paths and time zone
gw2kcop prepare 2026-11  # one folder per active account in accounts.csv
# put each export in its account folder, then:
gw2kcop transactions import 2026-11 --dry-run   # check, write nothing
gw2kcop transactions import 2026-11             # import; done files move to _zaimportowane/
```
Instruments are matched through `<data>/instrument_aliases.csv` (`institution,alias,ticker`);
an unknown alias stops that file and the command prints the lines to add.
Times: each transaction keeps `timestamp_utc` when the export has a time, and a local
`date` in the configured `timezone` (default `Europe/Warsaw`), used for monthly reports.

## Anonymizer
`gw2kcop anonymize ~/Finanse/Eksporty/2026-11/ANN-BROKER-PLN --rows 20` writes a copy of each
`.csv` export to `paths.samples` with:
- the same encoding, BOM, delimiter, quoting, line endings and number formats;
- amounts multiplied by one random factor per file (0.30-3.00; balances stay consistent),
  quantity columns kept;
- account numbers replaced by stable pseudonyms that keep their layout and never have a valid
  checksum; PESEL, NIP, card numbers, e-mails and phone numbers replaced by tags;
- your own terms (names, employer, broker account ids) from `terms.txt` and the
  `export_identifier` column of `accounts.csv` replaced everywhere;
- ISINs, tickers, dates, ids and operation keywords kept, so parsers still work on the sample.

The secret mapping (`mapping.json`, mode 600) stays in `paths.anonymizer`. The run ends with a
report: lines marked `?` need your eyes, lines marked `!` mean the sample is not safe yet.
Numbers inside free text (e.g. `25 x 174.88`) are not scaled. Always read a sample before sharing it.

## Supported formats (planned)
Simple CSV exports are described by one TOML file each in
`src/gw2kcop/transactions/parsers/<country>/<institution>/<export>.toml` and read by a generic engine.
Complex formats get Python code.

| Institution | Export | Approach |
|---|---|---|
| mBank eMakler / mDM | transaction and cash history (CSV); valuation history planned | TOML, **works** |
| Erste Brokerage (PL) | trade report, cash and securities history (CSV) | TOML |
| Bossa | `financeHistory.csv` (trades, deposits, dividends; `hisPW.csv` is not needed) | TOML, **works** on synthetic data |
| PPK ING | transaction history (CSV) | TOML |
| XTB | account statement (XLSX), or the multi-account ZIP dropped into the month folder or a folder of its own such as `OWNA-XTB/` (each statement goes to the account whose `export_identifier` is its account number) | code, **works** (no sales yet) |
| PPK Nationale-Nederlanden | transaction history (XLS) | code |

## Install (development)
Requires Python 3.11 or newer.

```
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
python -m pip install --require-hashes -r requirements-dev.lock
python -m pip install --no-deps --no-build-isolation -e .
pre-commit install                   # needs pre-commit installed separately, e.g. via pipx
python -m pytest
```

## Data safety
- Never commit real exports. `.gitignore` blocks data files by default, and a data guard
  (`tools/data_guard.py`) runs as a pre-commit hook and in CI.
- Test fixtures in `tests/fixtures/` are synthetic and marked `SYNTHETIC`.
- See [SECURITY.md](SECURITY.md).

## Licence
MIT, see [LICENSE](LICENSE).
