# Security policy

## Reporting a vulnerability
Please use GitHub **private vulnerability reporting**: the "Security" tab of this repository,
then "Report a vulnerability". Do not open a public issue.
I aim to acknowledge reports within 7 days.

**Never attach real bank or broker exports** to a report or issue. If a format problem needs
a sample, create one with `gw2kcop anonymize` and check it by hand before sharing.

## Supported versions
Only the latest commit on `main` is supported until the first release.

## Scope
`gw2kcop` processes files the user has already downloaded. It never handles bank
credentials, never logs in to banks or brokers and never scrapes websites. Reports about
parsing untrusted files (malformed CSV/XLSX/XLS, spreadsheet formula injection in outputs,
path handling) are in scope.

## How the project protects data
- Data files are ignored by default (`.gitignore`); only synthetic fixtures are allowed.
- A data guard blocks account numbers, PESEL-like numbers, card numbers, e-mail addresses
  and files over 1 MB, locally (pre-commit) and in CI.
- Secret scanning with push protection is enabled on the repository.

## Supply chain
- Runtime dependencies are limited to the standard library, `openpyxl`, `defusedxml` and `xlrd`,
  plus optional `yfinance`. Adding a dependency requires a written justification in the pull request.
- Dependencies are pinned with hashes (`requirements*.lock`) and installed with `--require-hashes`.
- CI runs `pip-audit` on every pull request and weekly.
- GitHub Actions are pinned to full commit SHAs and run with read-only permissions.
- Updates arrive through Dependabot pull requests and are reviewed one by one.
