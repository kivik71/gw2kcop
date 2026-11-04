# gw2kcop
(gw 2k COP)

**Parsers for (Polish) bank and broker exports to a common transactions CSV**

> Status: early development (skeleton). Commands exist but are not implemented yet.

## What it does
`gw2kcop` reads the files you download from your bank or broker and turns them into one
consistent `transactions.csv`, plus daily FX rates (NBP) and closing prices.
Everything runs **locally**. The tool never logs in to a bank, never scrapes websites and
never stores passwords.

| Command | Purpose |
|---|---|
| `gw2kcop prepare RRRR-MM` | create export folders for a month: `RRRR-MM/<account_id>/` |
| `gw2kcop transactions` | parse exports, deduplicate, append new rows to `transactions.csv` |
| `gw2kcop fx` | download NBP exchange rates |
| `gw2kcop prices` | download daily closing prices (optional Yahoo adapter) |
| `gw2kcop anonymize` | make an anonymized sample from a real export (for bug reports) |

## Supported formats (planned)
Simple CSV exports are described by one TOML file each in
`src/gw2kcop/transactions/parsers/<country>/<institution>/<export>.toml` and read by a generic engine.
Complex formats get Python code.

| Institution | Export | Approach |
|---|---|---|
| mBank eMakler / mDM | transaction, cash and valuation history (CSV) | TOML |
| Erste Brokerage (PL) | trade report, cash and securities history (CSV) | TOML |
| Bossa | `financeHistory.csv` | TOML |
| PPK ING | transaction history (CSV) | TOML |
| XTB | account statement (XLSX) | code |
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
