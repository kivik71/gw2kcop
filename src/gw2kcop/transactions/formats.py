"""Export format definitions (one TOML file per export) and header-based detection.

Format files live in ``parsers/<country>/<institution>/<export>.toml`` inside the
package. A file is recognised by its exact header row, never by its file name.
Unknown keys in a format file are rejected, so a typo cannot silently change parsing.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from gw2kcop.io.dates import DATE_FORMATS, DATETIME_FORMATS

KINDS = ("trades", "cash")
_TOP_KEYS = {
    "format", "institution", "kind", "delimiter", "decimal", "header", "timezone",
    "datetime_format", "date_only", "currency", "columns", "sides", "directions",
    "checks", "rules",
}
_COLUMN_KEYS = {
    "trades": {"instrument", "market", "side", "quantity", "price", "price_currency",
               "gross", "fee", "time", "currency", "fx_rate"},
    "cash": {"date", "direction", "description", "amount", "balance_before", "balance_after"},
}
_REQUIRED_COLUMNS = {
    "trades": {"instrument", "side", "quantity", "price", "price_currency", "gross", "time", "currency"},
    "cash": {"date", "direction", "description", "amount"},
}
_CHECK_KEYS = {"gross_equals_qty_price_fx", "fee_only_same_currency", "balance_chain"}
_RULE_KEYS = {"pattern", "direction", "type", "group"}
_RULE_TYPES = {"deposit", "withdrawal", "dividend", "interest", "fee", "tax", "charge", "other"}


class FormatError(ValueError):
    """A format file is invalid."""


@dataclass(frozen=True)
class Rule:
    pattern: re.Pattern[str]
    direction: str  # credit, debit or any
    type: str
    group: str = ""


@dataclass(frozen=True)
class FormatSpec:
    name: str
    institution: str
    kind: str
    delimiter: str
    decimal: str
    header: tuple[str, ...]
    timezone: str
    datetime_format: str
    date_only: bool
    currency: str  # "column" or "account"
    columns: dict[str, str]
    sides: dict[str, str] = field(default_factory=dict)
    directions: dict[str, str] = field(default_factory=dict)
    checks: dict[str, object] = field(default_factory=dict)
    rules: tuple[Rule, ...] = ()
    source: str = ""


def _require(cond: bool, where: str, message: str) -> None:
    if not cond:
        raise FormatError(f"{where}: {message}")


def parse_spec(data: dict, where: str) -> FormatSpec:
    unknown = set(data) - _TOP_KEYS
    _require(not unknown, where, f"unknown keys {sorted(unknown)}")
    kind = data.get("kind")
    _require(kind in KINDS, where, f"kind must be one of {KINDS}")
    header = tuple(data.get("header") or ())
    _require(len(header) > 1 and len(set(header)) == len(header), where, "header must list unique columns")
    columns = dict(data.get("columns") or {})
    _require(not set(columns) - _COLUMN_KEYS[kind], where, f"unknown columns {sorted(set(columns) - _COLUMN_KEYS[kind])}")
    _require(_REQUIRED_COLUMNS[kind] <= set(columns), where,
             f"missing columns {sorted(_REQUIRED_COLUMNS[kind] - set(columns))}")
    for key, col in columns.items():
        _require(col in header, where, f"column {key} = {col!r} is not in the header")
    fmt = data.get("datetime_format", "")
    _require(fmt in DATETIME_FORMATS or fmt in DATE_FORMATS, where, f"unknown datetime_format {fmt!r}")
    _require(data.get("decimal") in (",", "."), where, "decimal must be ',' or '.'")
    checks = dict(data.get("checks") or {})
    _require(not set(checks) - _CHECK_KEYS, where, f"unknown checks {sorted(set(checks) - _CHECK_KEYS)}")
    currency = data.get("currency", "column")
    _require(currency in ("column", "account"), where, "currency must be 'column' or 'account'")
    if kind == "trades":
        sides = dict(data.get("sides") or {})
        _require(sides and set(sides.values()) <= {"buy", "sell"}, where, "sides must map to buy/sell")
    else:
        sides = {}
    directions = dict(data.get("directions") or {})
    rules: list[Rule] = []
    if kind == "cash":
        _require(directions and set(directions.values()) <= {"credit", "debit"}, where,
                 "directions must map to credit/debit")
        for n, raw in enumerate(data.get("rules") or (), start=1):
            _require(not set(raw) - _RULE_KEYS, where, f"rule {n}: unknown keys {sorted(set(raw) - _RULE_KEYS)}")
            _require(raw.get("type") in _RULE_TYPES, where, f"rule {n}: type must be one of {sorted(_RULE_TYPES)}")
            direction = raw.get("direction", "any")
            _require(direction in ("credit", "debit", "any"), where, f"rule {n}: bad direction")
            try:
                pattern = re.compile(raw["pattern"])
            except (KeyError, re.error) as exc:
                raise FormatError(f"{where}: rule {n}: bad pattern ({exc})") from None
            rules.append(Rule(pattern, direction, raw["type"], raw.get("group", "")))
        _require(rules, where, "cash formats need [[rules]]")
    return FormatSpec(
        name=data.get("format", ""),
        institution=data.get("institution", ""),
        kind=kind,
        delimiter=data.get("delimiter", ";"),
        decimal=data["decimal"],
        header=header,
        timezone=data.get("timezone", "Europe/Warsaw"),
        datetime_format=fmt,
        date_only=bool(data.get("date_only", False)),
        currency=currency,
        columns=columns,
        sides=sides,
        directions=directions,
        checks=checks,
        rules=tuple(rules),
        source=where,
    )


def load_spec(path: Path) -> FormatSpec:
    with open(path, "rb") as handle:
        return parse_spec(tomllib.load(handle), path.name)


def builtin_specs() -> list[FormatSpec]:
    """All format files shipped with the package."""
    root = resources.files("gw2kcop.transactions.parsers")
    specs: list[FormatSpec] = []

    def walk(node, prefix: str) -> None:
        for child in sorted(node.iterdir(), key=lambda c: c.name):
            if child.is_dir():
                walk(child, f"{prefix}{child.name}/")
            elif child.name.endswith(".toml"):
                specs.append(parse_spec(tomllib.loads(child.read_text(encoding="utf-8")), prefix + child.name))

    walk(root, "")
    return specs


def detect(header: list[str], specs: list[FormatSpec], institution: str) -> FormatSpec | None:
    """The format whose header matches exactly, among the institution's formats."""
    cleaned = [h.strip() for h in header]
    while cleaned and not cleaned[-1]:
        cleaned.pop()  # a trailing delimiter gives one empty column
    cleaned = tuple(cleaned)
    for spec in specs:
        if spec.institution == institution and spec.header == cleaned:
            return spec
    return None
