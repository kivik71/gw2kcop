"""User configuration (``config.toml``).

Location, first match wins:
1. ``--config PATH`` on the command line
2. environment variable ``GW2KCOP_CONFIG``
3. Windows: ``%APPDATA%\\gw2kcop\\config.toml``; elsewhere:
   ``$XDG_CONFIG_HOME/gw2kcop/config.toml`` or ``~/.config/gw2kcop/config.toml``

The file holds only paths and settings, never data or secrets. Unknown keys are
rejected so that a typo cannot silently fall back to a default.
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ENV_VAR = "GW2KCOP_CONFIG"

TEMPLATE = """\
# gw2kcop configuration. Paths may start with ~ (home folder).
# Keep this file outside the git repository.

# Time zone used to derive the local `date` of each transaction.
timezone = "Europe/Warsaw"

[paths]
# Monthly export folders are created here: <exports>/RRRR-MM/<account_id>/
exports = "~/Finanse/Eksporty"
# Master files: transactions.csv, fx_rates.csv, prices.csv, ...
data = "~/Finanse/Dane"
# List of accounts (default: <data>/accounts.csv)
accounts = "~/Finanse/Dane/accounts.csv"
"""

_TOP_KEYS = {"timezone", "paths"}
_PATH_KEYS = {"exports", "data", "accounts"}


class ConfigError(ValueError):
    """The configuration file is missing or invalid."""


@dataclass(frozen=True)
class Config:
    source: Path
    timezone: ZoneInfo
    exports_dir: Path
    data_dir: Path
    accounts_file: Path


def default_config_path() -> Path:
    if sys.platform == "win32" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "gw2kcop" / "config.toml"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "gw2kcop" / "config.toml"


def resolve_config_path(cli_path: str | Path | None = None) -> Path:
    if cli_path:
        return Path(cli_path).expanduser()
    if os.environ.get(ENV_VAR):
        return Path(os.environ[ENV_VAR]).expanduser()
    return default_config_path()


def _path(value: object, key: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"paths.{key} must be a non-empty string")
    return Path(value).expanduser()


def parse_config(text: str, source: Path) -> Config:
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{source}: invalid TOML: {exc}") from None
    unknown = set(raw) - _TOP_KEYS
    if unknown:
        raise ConfigError(f"{source}: unknown keys: {', '.join(sorted(unknown))}")
    tz_name = raw.get("timezone", "Europe/Warsaw")
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise ConfigError(f"{source}: unknown timezone {tz_name!r}") from None
    paths = raw.get("paths")
    if not isinstance(paths, dict):
        raise ConfigError(f"{source}: missing [paths] section")
    unknown = set(paths) - _PATH_KEYS
    if unknown:
        raise ConfigError(f"{source}: unknown keys in [paths]: {', '.join(sorted(unknown))}")
    for key in ("exports", "data"):
        if key not in paths:
            raise ConfigError(f"{source}: paths.{key} is required")
    data_dir = _path(paths["data"], "data")
    accounts = _path(paths["accounts"], "accounts") if "accounts" in paths else data_dir / "accounts.csv"
    return Config(
        source=source,
        timezone=tz,
        exports_dir=_path(paths["exports"], "exports"),
        data_dir=data_dir,
        accounts_file=accounts,
    )


def load_config(cli_path: str | Path | None = None) -> Config:
    path = resolve_config_path(cli_path)
    if not path.is_file():
        raise ConfigError(
            f"configuration file not found: {path}\n"
            "Create it with:  gw2kcop config init"
        )
    return parse_config(path.read_text(encoding="utf-8"), path)


def write_template(path: Path) -> bool:
    """Write the template to ``path``. Return False if the file already exists."""
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(TEMPLATE)
    return True
