"""Anonymize one CSV export, keeping its format 1:1."""

from __future__ import annotations

import re
import secrets
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from gw2kcop.anonymize import patterns as pt
from gw2kcop.anonymize.csvtokens import Record, render, tokenize
from gw2kcop.anonymize.mapping import Mapping
from gw2kcop.anonymize.numfmt import has_decimals, is_number, scale_text
from gw2kcop.io.csvio import sniff_delimiter
from gw2kcop.io.text import decode_bytes

# Header words that mark a quantity column: kept unless --scale-quantities,
# because scaling quantity and price together would break value = qty x price.
QUANTITY_HEADERS = re.compile(
    r"\b(?:ilość|liczba|quantity|volume|units?|jednostek|shares|szt)\b", re.IGNORECASE
)
# Compact dates (YYYYMMDD) are not identifiers and stay as they are.
_COMPACT_DATE_RE = re.compile(r"(?:19|20)\d\d(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])")
SAFE_FACTOR_RANGE = (30, 300)  # factor = n / 100, i.e. 0.30 .. 3.00


@dataclass
class Options:
    rows: int | None = None
    keep_last: int = 0
    decimal_sep: str | None = None
    scale_quantities: bool = False
    factor: Decimal | None = None  # tests only; normally random per file


@dataclass
class FileReport:
    source: Path
    output: Path | None = None
    encoding: str = ""
    delimiter: str = ""
    decimal_sep: str = ""
    data_rows_in: int = 0
    data_rows_out: int = 0
    replaced: Counter = field(default_factory=Counter)
    scaled_columns: list[str] = field(default_factory=list)
    kept_quantity_columns: list[str] = field(default_factory=list)
    suspicious: list[str] = field(default_factory=list)


def random_factor() -> Decimal:
    low, high = SAFE_FACTOR_RANGE
    n = low + secrets.randbelow(high - low + 1)
    if n == 100:
        n += 7  # never keep real amounts
    return Decimal(n) / 100


def detect_decimal_sep(records: list[Record]) -> str:
    comma = dot = 0
    for record in records:
        for value in record.values:
            if has_decimals(value, ","):
                comma += 1
            if has_decimals(value, "."):
                dot += 1
    return "." if dot > comma else ","


def _split_table(records: list[Record]) -> tuple[int | None, list[int]]:
    """Return (header index, data row indexes).

    The table is the longest unbroken block of rows with the dominant column count;
    its first row is the header. Metadata rows above it (even with the same number
    of columns) are separated from it by a blank or differently shaped line.
    """
    widths = Counter(len(r.fields) for r in records if len(r.fields) > 1)
    if not widths:
        return None, []
    width = max(widths.items(), key=lambda kv: (kv[1], kv[0]))[0]
    best: list[int] = []
    current: list[int] = []
    for i, record in enumerate(records):
        if len(record.fields) == width:
            current.append(i)
            if len(current) > len(best):
                best = list(current)
        else:
            current = []
    return best[0], best[1:]


def _classify_columns(records, header_idx, data_idx, decimal_sep, scale_quantities):
    header = records[header_idx].values if header_idx is not None else []
    width = len(header)
    scale: set[int] = set()
    kept_qty: list[str] = []
    kept_qty_cols: set[int] = set()
    scaled_names: list[str] = []
    for col in range(width):
        cells = [records[i].fields[col].value for i in data_idx if records[i].fields[col].value.strip()]
        if not cells:
            continue
        numeric = sum(1 for c in cells if is_number(c, decimal_sep))
        decimals = sum(1 for c in cells if has_decimals(c, decimal_sep))
        if numeric / len(cells) < 0.8 or decimals == 0:
            continue
        name = header[col].strip()
        if QUANTITY_HEADERS.search(name) and not scale_quantities:
            kept_qty.append(name)
            kept_qty_cols.add(col)
            continue
        scale.add(col)
        scaled_names.append(name)
    return scale, scaled_names, kept_qty, kept_qty_cols


def _pick_rows(records, data_idx, limit):
    """Keep up to ``limit`` data rows, first one of each distinct operation type."""
    if limit is None or len(data_idx) <= limit:
        return set(data_idx)
    # Type column: the text column with the fewest distinct values (> 1).
    width = len(records[data_idx[0]].fields)
    best_col, best_distinct = None, None
    for col in range(width):
        values = [records[i].fields[col].value for i in data_idx]
        distinct = len(set(values))
        if 1 < distinct < len(values) and not any(ch.isdigit() for ch in "".join(values[:20])):
            if best_distinct is None or distinct < best_distinct:
                best_col, best_distinct = col, distinct
    chosen: list[int] = []
    if best_col is not None:
        seen: set[str] = set()
        for i in data_idx:
            value = records[i].fields[best_col].value
            if value not in seen:
                seen.add(value)
                chosen.append(i)
    for i in data_idx:
        if len(chosen) >= limit:
            break
        if i not in chosen:
            chosen.append(i)
    return set(chosen[:limit])


class TextScrubber:
    """Replace identifiers and personal data inside a text value."""

    def __init__(self, mapping: Mapping, terms: list[str], keep_last: int, report: FileReport):
        self.mapping = mapping
        self.keep_last = keep_last
        self.report = report
        self._made: set[str] = set()  # pseudonyms produced while scrubbing one value
        self.term_res = [
            (term, re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)", re.IGNORECASE)) for term in terms
        ]

    def __call__(self, value: str) -> str:
        out = value
        self._made = set()
        for term, rx in self.term_res:
            if rx.search(out):
                out = rx.sub(lambda m, t=term: self._term(t), out)
        out = pt.IBAN_RE.sub(lambda m: self._digits(m.group(0), "account"), out)
        out = pt.NRB_RE.sub(lambda m: self._digits(m.group(0), "account"), out)
        out = pt.EMAIL_RE.sub(lambda m: self._tag("EMAIL", m.group(0), "email"), out)
        out = pt.PHONE_RE.sub(lambda m: self._tag("TELEFON", m.group(0), "phone"), out)
        out = pt.LONG_DIGITS_RE.sub(self._digit_run, out)
        return out

    def _term(self, term: str) -> str:
        self.report.replaced["own term"] += 1
        return self.mapping.tag("OSOBA", term) if term[:1].isalpha() else self._digits(term, "identifier")

    def _digits(self, value: str, kind: str) -> str:
        self.report.replaced[kind] += 1
        pseudonym = self.mapping.digits(value, self.keep_last)
        if kind == "account" and pt.account_checksum_ok(pseudonym):
            # Make sure a pseudonym can never be a valid account number.
            pseudonym = self.mapping.digits(value + "#", self.keep_last)
            if pt.account_checksum_ok(pseudonym):
                pos = next(i for i, ch in enumerate(pseudonym) if ch.isdigit())
                pseudonym = pseudonym[:pos] + str((int(pseudonym[pos]) + 1) % 10) + pseudonym[pos + 1:]
            self.mapping.pseudonyms[f"digits:{self.keep_last}:{value}"] = pseudonym
        self._made.add(pseudonym)
        return pseudonym

    def _tag(self, kind: str, value: str, label: str) -> str:
        self.report.replaced[label] += 1
        return self.mapping.tag(kind, value)

    def _digit_run(self, m: re.Match[str]) -> str:
        """Any run of 6+ digits: personal ids get a tag, other ids a digit pseudonym.

        Order, trade and participant numbers are pseudonymized with the same
        mapping everywhere, so a join such as ZLC in a description = order id
        column still works in the sample.
        """
        digits = m.group(0)
        if any(digits in made for made in self._made):
            return digits
        if 9 <= len(digits) <= 19:
            if pt.is_pesel(digits):
                return self._tag("PESEL", digits, "PESEL")
            if pt.is_nip(digits):
                return self._tag("NIP", digits, "NIP")
            if pt.is_luhn(digits):
                self.report.replaced["card"] += 1
                return f"[KARTA_****{digits[-4:]}]" if self.keep_last else self.mapping.tag("KARTA", digits)
        if len(digits) == 8 and _COMPACT_DATE_RE.fullmatch(digits):
            return digits
        self.report.replaced["id"] += 1
        pseudonym = self.mapping.digits(digits)
        salt = 0
        while _looks_personal(pseudonym) and salt < 20:
            # Never let a plain id turn into something that looks like a PESEL or card.
            salt += 1
            pseudonym = self.mapping.digits(f"{digits}#{salt}")[: len(digits)]
            self.mapping.pseudonyms[f"digits:0:{digits}"] = pseudonym
        self._made.add(pseudonym)
        return pseudonym


def _looks_personal(digits: str) -> bool:
    return 9 <= len(digits) <= 19 and (pt.is_pesel(digits) or pt.is_nip(digits) or pt.is_luhn(digits))


def anonymize_bytes(raw: bytes, source: Path, mapping: Mapping, terms: list[str],
                    options: Options) -> tuple[bytes, FileReport]:
    report = FileReport(source=source)
    decoded = decode_bytes(raw)
    report.encoding = decoded.encoding
    delimiter = sniff_delimiter(decoded.text)
    report.delimiter = delimiter
    records = tokenize(decoded.text, delimiter)
    decimal_sep = options.decimal_sep or detect_decimal_sep(records)
    report.decimal_sep = decimal_sep
    header_idx, data_idx = _split_table(records)
    report.data_rows_in = len(data_idx)
    scale_cols, scaled_names, kept_qty, qty_cols = _classify_columns(
        records, header_idx, data_idx, decimal_sep, options.scale_quantities
    )
    report.scaled_columns, report.kept_quantity_columns = scaled_names, kept_qty
    keep_rows = _pick_rows(records, data_idx, options.rows)
    data_set = set(data_idx)
    factor = options.factor or random_factor()
    scrub = TextScrubber(mapping, terms, options.keep_last, report)

    out_records: list[Record] = []
    for i, record in enumerate(records):
        if i in data_set and i not in keep_rows:
            continue
        for col, f in enumerate(record.fields):
            if i in data_set and col in scale_cols:
                scaled = scale_text(f.value, factor, decimal_sep)
                if scaled is not None:
                    if scaled != f.value:
                        report.replaced["amount"] += 1
                    f.value = scaled
                    continue
            if i in data_set and col in qty_cols and is_number(f.value, decimal_sep):
                continue
            f.value = scrub(f.value)
        out_records.append(record)
    # Keep the file's final line ending style even if its last data row was dropped.
    if out_records and records and out_records[-1] is not records[-1]:
        out_records[-1].ending = records[-1].ending
    report.data_rows_out = len([i for i in data_idx if i in keep_rows])
    report.suspicious = find_suspicious(out_records, header_idx, data_set, scale_cols,
                                        set(mapping.pseudonyms.values()))

    text = render(out_records, delimiter)
    encoding = decoded.encoding
    if encoding == "utf-8-sig":
        body = text.encode("utf-8-sig")
    elif encoding == "utf-16":
        body = text.encode("utf-16-le" if raw[:2] == b"\xff\xfe" else "utf-16-be")
        body = raw[:2] + body
    else:
        body = text.encode(encoding)
    return body, report


_WORD_RE = re.compile(r"\b[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]{2,}\b")


def find_suspicious(records, header_idx, data_set, scale_cols, pseudonyms=frozenset()) -> list[str]:
    """Things a human should look at before sharing: metadata text and long digit runs.

    Pure-number cells in the table (ids) and pseudonyms made by the mapping are skipped.
    """
    notes: list[str] = []
    for i, record in enumerate(records):
        if i == header_idx:
            continue
        for col, f in enumerate(record.fields):
            if i in data_set and col in scale_cols:
                continue
            value = f.value
            if i in data_set and value.strip().isdigit():
                continue
            runs = [m.group(0) for m in pt.LONG_DIGITS_RE.finditer(value)]
            if any(not any(r in p for p in pseudonyms) for r in runs):
                notes.append(f"line {i + 1}: long number left: {value[:60]!r}")
            elif i not in data_set and _WORD_RE.search(value):
                notes.append(f"line {i + 1} (above/below the table): {value[:60]!r}")
    return notes[:50]


def check_text(text: str, mapping: Mapping | None) -> list[str]:
    """Scan a finished sample for identifiers that should not be there."""
    findings: list[str] = []
    for n, line in enumerate(text.splitlines(), start=1):
        for m in list(pt.IBAN_RE.finditer(line)) + list(pt.NRB_RE.finditer(line)):
            if pt.account_checksum_ok(m.group(0)):
                findings.append(f"line {n}: account number (valid checksum)")
                break
        if pt.EMAIL_RE.search(line):
            findings.append(f"line {n}: e-mail address")
        if pt.PHONE_RE.search(line):
            findings.append(f"line {n}: phone number")
        for m in pt.DIGIT_RUN_RE.finditer(line):
            d = m.group(0)
            if pt.is_pesel(d):
                findings.append(f"line {n}: PESEL-like number")
            elif pt.is_nip(d):
                findings.append(f"line {n}: NIP-like number")
            elif pt.is_luhn(d):
                findings.append(f"line {n}: card-like number")
    if mapping:
        lowered = text.casefold()
        for original in mapping.originals():
            if original.isdigit():
                found = re.search(r"(?<!\d)" + original + r"(?!\d)", text) is not None
            else:
                found = len(original) >= 3 and original.casefold() in lowered
            if found:
                findings.append(f"original value from the mapping still present: {original[:3]}...")
    return findings


def anonymize_file(source: Path, output_dir: Path, mapping: Mapping, terms: list[str],
                   options: Options) -> FileReport:
    output = output_dir / source.name
    if output.resolve() == source.resolve():
        raise ValueError("output would overwrite the original export")
    body, report = anonymize_bytes(source.read_bytes(), source, mapping, terms, options)
    output_dir.mkdir(parents=True, exist_ok=True)
    output.write_bytes(body)
    report.output = output
    return report
