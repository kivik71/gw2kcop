"""Export format definitions (one TOML file per export) and header-based detection.

Format files live in ``parsers/<country>/<institution>/<export>.toml`` inside the
package. A file is recognised by its exact header row, never by its file name.
Unknown keys in a format file are rejected, so a typo cannot silently change parsing.

A header may repeat a name (eMakler has three ``Waluta`` columns). Repeated names are
addressed as ``Name[1]``, ``Name[2]``... in ``[columns]``; a bare repeated name is an error.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from gw2kcop.io.dates import DATE_FORMATS, DATETIME_FORMATS

KINDS = ("trades", "cash", "valuations")
_TOP_KEYS = {
    "format", "institution", "kind", "delimiter", "decimal", "header", "timezone",
    "datetime_format", "date_only", "currency", "columns", "sides", "directions",
    "checks", "rules", "preamble", "overflow_column",
}
_COLUMN_KEYS = {
    "trades": {"instrument", "market", "side", "quantity", "price", "price_currency",
               "gross", "fee", "fee_currency", "time", "currency", "fx_rate"},
    "cash": {"date", "direction", "description", "amount", "balance_before", "balance_after"},
    "valuations": {"date", "value"},
}
_REQUIRED_COLUMNS = {
    "trades": {"instrument", "side", "quantity", "price", "price_currency", "gross", "time", "currency"},
    "cash": {"date", "description", "amount"},  # no direction column: the sign of amount decides
    "valuations": {"date", "value"},
}
_CHECK_KEYS = {"gross_equals_qty_price_fx", "fee_only_same_currency", "balance_chain"}
_RULE_KEYS = {"pattern", "direction", "type", "group"}
# "ignore": a recognised row that is not written (its effect comes from another export).
_RULE_TYPES = {"deposit", "withdrawal", "dividend", "interest", "fee", "tax", "charge", "other", "ignore"}


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
    keys: tuple[str, ...] = ()  # header with repeated names numbered: Waluta[1], Waluta[2]
    preamble: bool = False      # free text may precede the header row
    overflow_column: str = ""   # column that may contain unquoted delimiters

    def __post_init__(self) -> None:
        if not self.keys:
            object.__setattr__(self, "keys", column_keys(self.header))


def column_keys(header: tuple[str, ...]) -> tuple[str, ...]:
    """Header names, with ``[n]`` appended to every occurrence of a repeated name."""
    counts = {name: header.count(name) for name in header}
    seen: dict[str, int] = {}
    keys = []
    for name in header:
        if counts[name] == 1:
            keys.append(name)
        else:
            seen[name] = seen.get(name, 0) + 1
            keys.append(f"{name}[{seen[name]}]")
    return tuple(keys)


def _require(cond: bool, where: str, message: str) -> None:
    if not cond:
        raise FormatError(f"{where}: {message}")


def parse_spec(data: dict, where: str) -> FormatSpec:
    unknown = set(data) - _TOP_KEYS
    _require(not unknown, where, f"unknown keys {sorted(unknown)}")
    kind = data.get("kind")
    _require(kind in KINDS, where, f"kind must be one of {KINDS}")
    header = tuple(data.get("header") or ())
    _require(len(header) > 1 and all(isinstance(h, str) and h for h in header), where, "header must list columns")
    keys = column_keys(header)
    columns = dict(data.get("columns") or {})
    _require(not set(columns) - _COLUMN_KEYS[kind], where, f"unknown columns {sorted(set(columns) - _COLUMN_KEYS[kind])}")
    _require(_REQUIRED_COLUMNS[kind] <= set(columns), where,
             f"missing columns {sorted(_REQUIRED_COLUMNS[kind] - set(columns))}")
    for key, col in columns.items():
        _require(col in keys, where, f"column {key} = {col!r} is not in the header"
                 + (" (repeated name: use Name[1], Name[2]...)" if col in header else ""))
    overflow = data.get("overflow_column", "")
    _require(not overflow or overflow in keys, where, f"overflow_column {overflow!r} is not in the header")
    fmt = data.get("datetime_format", "")
    _require(fmt in DATETIME_FORMATS or fmt in DATE_FORMATS, where, f"unknown datetime_format {fmt!r}")
    _require(data.get("decimal") in (",", "."), where, "decimal must be ',' or '.'")
    checks = dict(data.get("checks") or {})
    _require(not set(checks) - _CHECK_KEYS, where, f"unknown checks {sorted(set(checks) - _CHECK_KEYS)}")
    currency = data.get("currency", "column")
    if kind == "valuations":
        # broker_valuations.csv holds value_pln only: the format must state PLN explicitly
        _require(currency == "PLN", where, "valuations need currency = 'PLN'")
    else:
        _require(currency in ("column", "account"), where, "currency must be 'column' or 'account'")
    if kind == "trades":
        sides = dict(data.get("sides") or {})
        _require(sides and set(sides.values()) <= {"buy", "sell"}, where, "sides must map to buy/sell")
    else:
        sides = {}
    directions = dict(data.get("directions") or {})
    rules: list[Rule] = []
    if kind == "cash":
        if "direction" in columns:
            _require(directions and set(directions.values()) <= {"credit", "debit"}, where,
                     "directions must map to credit/debit")
        else:
            _require(not directions, where, "directions need a direction column")
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
        keys=keys,
        preamble=bool(data.get("preamble", False)),
        overflow_column=overflow,
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


def clean_header(row: list[str]) -> tuple[str, ...]:
    cleaned = [h.strip() for h in row]
    while cleaned and not cleaned[-1]:
        cleaned.pop()  # a trailing delimiter gives one empty column
    return tuple(cleaned)


def detect(header: list[str], specs: list[FormatSpec], institution: str,
           after_preamble: bool = False) -> FormatSpec | None:
    """The format whose header matches exactly, among the institution's formats.

    ``after_preamble``: the row is not the first non-empty row of the file, so only
    formats that allow a preamble can match.
    """
    cleaned = clean_header(header)
    for spec in specs:
        if (spec.institution == institution and spec.header == cleaned
                and (spec.preamble or not after_preamble)):
            return spec
    return None
