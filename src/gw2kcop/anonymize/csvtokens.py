"""Lossless CSV tokenizer: split into records and fields and join back byte-for-byte.

The standard ``csv`` module normalises quoting and line endings when writing, so
it cannot guarantee a 1:1 copy. This tokenizer remembers, for every field,
whether it was quoted, and for every record, its line ending.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Field:
    value: str
    quoted: bool = False


@dataclass
class Record:
    fields: list[Field] = field(default_factory=list)
    ending: str = ""  # "\r\n", "\n", "\r" or "" for the last line

    @property
    def values(self) -> list[str]:
        return [f.value for f in self.fields]


def tokenize(text: str, delimiter: str) -> list[Record]:
    records: list[Record] = []
    record = Record()
    value: list[str] = []
    quoted = False
    in_quotes = False
    i = 0
    n = len(text)
    at_field_start = True
    while i < n:
        ch = text[i]
        if in_quotes:
            if ch == '"':
                if i + 1 < n and text[i + 1] == '"':
                    value.append('"')
                    i += 2
                    continue
                in_quotes = False
                i += 1
                continue
            value.append(ch)
            i += 1
            continue
        if ch == '"' and at_field_start:
            in_quotes = True
            quoted = True
            at_field_start = False
            i += 1
            continue
        if ch == delimiter:
            record.fields.append(Field("".join(value), quoted))
            value, quoted, at_field_start = [], False, True
            i += 1
            continue
        if ch in "\r\n":
            ending = "\r\n" if text.startswith("\r\n", i) else ch
            record.fields.append(Field("".join(value), quoted))
            record.ending = ending
            records.append(record)
            record, value, quoted, at_field_start = Record(), [], False, True
            i += len(ending)
            continue
        value.append(ch)
        at_field_start = False
        i += 1
    if value or quoted or record.fields:
        record.fields.append(Field("".join(value), quoted))
        records.append(record)
    return records


def _render_field(f: Field, delimiter: str) -> str:
    needs_quotes = f.quoted or any(c in f.value for c in (delimiter, '"', "\r", "\n"))
    if needs_quotes:
        return '"' + f.value.replace('"', '""') + '"'
    return f.value


def render(records: list[Record], delimiter: str) -> str:
    return "".join(
        delimiter.join(_render_field(f, delimiter) for f in r.fields) + r.ending for r in records
    )
