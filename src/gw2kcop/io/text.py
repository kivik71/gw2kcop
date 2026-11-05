"""Read export files as text with encoding detection.

Order of detection:
1. Byte order mark: UTF-8 (``utf-8-sig``), UTF-16 LE/BE.
2. Strict UTF-8.
3. Windows-1250 (Polish legacy exports), only if the bytes contain no control
   characters that Windows-1250 would map to junk.

Anything else raises ``UnknownEncodingError``: the caller must not guess further.
"""

from __future__ import annotations

import codecs
from dataclasses import dataclass
from pathlib import Path

MAX_FILE_BYTES = 50_000_000  # exports are small; a huge file is a mistake or an attack

_BOMS = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)
# Bytes undefined in Windows-1250; seeing them means the file is not CP1250 text.
_CP1250_UNDEFINED = {0x81, 0x83, 0x88, 0x90, 0x98}


class UnknownEncodingError(ValueError):
    """The file is not UTF-8/UTF-16/Windows-1250 text."""


@dataclass(frozen=True)
class DecodedText:
    text: str
    encoding: str
    had_bom: bool


def decode_bytes(raw: bytes) -> DecodedText:
    for bom, encoding in _BOMS:
        if raw.startswith(bom):
            return DecodedText(raw.decode(encoding), encoding, True)
    try:
        return DecodedText(raw.decode("utf-8"), "utf-8", False)
    except UnicodeDecodeError:
        pass
    if b"\x00" in raw or any(b in _CP1250_UNDEFINED for b in raw):
        raise UnknownEncodingError("not UTF-8, UTF-16 or Windows-1250 text")
    return DecodedText(raw.decode("cp1250"), "cp1250", False)


def read_text(path: str | Path) -> DecodedText:
    """Read ``path`` and decode it. Newlines are kept as in the file."""
    path = Path(path)
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise ValueError(f"{path.name}: file too large ({size} bytes)")
    return decode_bytes(path.read_bytes())
