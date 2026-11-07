"""Patterns for personal and financial identifiers in free text."""

from __future__ import annotations

import re

# Polish IBAN (PL + 26 digits) and bare NRB (26 digits), spaced in groups of 4 or not.
IBAN_RE = re.compile(r"(?<![A-Za-z0-9])PL ?\d{2}(?: ?\d{4}){6}(?![0-9])", re.IGNORECASE)
NRB_RE = re.compile(r"(?<![\dA-Za-z])\d{2}(?: ?\d{4}){6}(?![\dA-Za-z])")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# Phone numbers need +48 or separators, so plain 9-digit ids are not taken for phones.
PHONE_RE = re.compile(r"(?<![\dA-Za-z])(?:\+48[ -]?\d{3}[ -]?\d{3}[ -]?\d{3}|\d{3}[ -]\d{3}[ -]\d{3})(?![\dA-Za-z])")
DIGIT_RUN_RE = re.compile(r"(?<![\dA-Za-z])\d{9,19}(?![\dA-Za-z])")
LONG_DIGITS_RE = re.compile(r"(?<![\dA-Za-z.,])\d{6,}(?![\dA-Za-z]|[.,]\d)")

_PESEL_WEIGHTS = (1, 3, 7, 9, 1, 3, 7, 9, 1, 3)
_NIP_WEIGHTS = (6, 5, 7, 2, 3, 4, 5, 6, 7)


def is_pesel(digits: str) -> bool:
    if len(digits) != 11 or not digits.isdigit():
        return False
    total = sum(int(d) * w for d, w in zip(digits[:10], _PESEL_WEIGHTS))
    return (10 - total % 10) % 10 == int(digits[10])


def is_nip(digits: str) -> bool:
    if len(digits) != 10 or not digits.isdigit():
        return False
    check = sum(int(d) * w for d, w in zip(digits[:9], _NIP_WEIGHTS)) % 11
    return check != 10 and check == int(digits[9])


def is_luhn(digits: str) -> bool:
    if not 13 <= len(digits) <= 19 or not digits.isdigit():
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def account_checksum_ok(text: str) -> bool:
    """True if ``text`` (PL IBAN or 26-digit NRB, spaces allowed) has a valid IBAN checksum.

    Real account numbers always pass; pseudonyms are made to fail, so --check can
    tell them apart.
    """
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) != 26:
        return False
    check, bban = digits[:2], digits[2:]
    return int(bban + "2521" + check) % 97 == 1  # "PL" -> 25 21
