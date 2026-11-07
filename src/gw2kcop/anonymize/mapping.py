"""Secret, persistent pseudonym mapping (Guidelines: data class C4).

The mapping file holds a random secret and every original -> pseudonym pair, so
the same account or person gets the same pseudonym in every file and every run.
It is written with mode 600 inside a folder with mode 700 (macOS/Linux) and must
never leave the computer.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path

MAPPING_FILE = "mapping.json"
TERMS_FILE = "terms.txt"
VERSION = 1


class Mapping:
    def __init__(self, secret: bytes, pseudonyms: dict[str, str] | None = None,
                 tags: dict[str, str] | None = None) -> None:
        self._secret = secret
        self.pseudonyms: dict[str, str] = dict(pseudonyms or {})
        self.tags: dict[str, str] = dict(tags or {})

    @classmethod
    def new(cls) -> "Mapping":
        return cls(secrets.token_bytes(32))

    # --- pseudonyms that keep the format of the original -----------------------
    def _digest(self, value: str) -> bytes:
        return hmac.new(self._secret, value.encode("utf-8"), hashlib.sha256).digest()

    def digits(self, original: str, keep_last: int = 0) -> str:
        """Replace every digit in ``original`` with a pseudo-random digit.

        Letters, spaces and punctuation stay, so the length and layout are kept
        (``PL61 1090 ...`` -> ``PL27 4810 ...``). ``keep_last`` keeps that many
        trailing digits. The result is deterministic for a given secret.
        """
        key = f"digits:{keep_last}:{original}"
        if key in self.pseudonyms:
            return self.pseudonyms[key]
        digit_positions = [i for i, ch in enumerate(original) if ch.isdigit()]
        blocks = len(digit_positions) // 32 + 1
        stream = b"".join(self._digest(f"{key}:{i}") for i in range(blocks))
        keep = set(digit_positions[-keep_last:]) if keep_last else set()
        out = list(original)
        for n, pos in enumerate(digit_positions):
            if pos not in keep:
                out[pos] = str(stream[n] % 10)
        pseudonym = "".join(out)
        if pseudonym == original and digit_positions and len(keep) < len(digit_positions):
            first = next(p for p in digit_positions if p not in keep)
            out[first] = str((int(out[first]) + 1) % 10)
            pseudonym = "".join(out)
        self.pseudonyms[key] = pseudonym
        return pseudonym

    def tag(self, kind: str, original: str) -> str:
        """Stable placeholder such as ``[OSOBA_1]`` for a sensitive term."""
        key = f"{kind}:{original.casefold()}"
        if key not in self.tags:
            number = sum(1 for k in self.tags if k.startswith(f"{kind}:")) + 1
            self.tags[key] = f"[{kind}_{number}]"
        return self.tags[key]

    def originals(self) -> list[str]:
        """All original values known to the mapping (used by --check)."""
        values = [key.split(":", 2)[2] for key in self.pseudonyms]
        values += [key.split(":", 1)[1] for key in self.tags]
        return values

    # --- persistence --------------------------------------------------------------
    def to_json(self) -> str:
        return json.dumps(
            {"version": VERSION, "secret": self._secret.hex(),
             "pseudonyms": self.pseudonyms, "tags": self.tags},
            ensure_ascii=False, indent=1, sort_keys=True,
        )

    @classmethod
    def from_json(cls, text: str) -> "Mapping":
        raw = json.loads(text)
        if raw.get("version") != VERSION:
            raise ValueError("unsupported mapping file version")
        return cls(bytes.fromhex(raw["secret"]), raw.get("pseudonyms"), raw.get("tags"))


def _private_dir(folder: Path) -> None:
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name == "posix":
        folder.chmod(0o700)


def load_mapping(folder: Path) -> Mapping:
    path = folder / MAPPING_FILE
    if path.is_file():
        return Mapping.from_json(path.read_text(encoding="utf-8"))
    return Mapping.new()


def save_mapping(folder: Path, mapping: Mapping) -> Path:
    """Write the mapping atomically with mode 600."""
    _private_dir(folder)
    path = folder / MAPPING_FILE
    tmp = folder / (MAPPING_FILE + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(mapping.to_json())
    os.replace(tmp, path)
    if os.name == "posix":
        path.chmod(0o600)
    return path


def load_terms(folder: Path) -> list[str]:
    """Own sensitive terms (names, street, employer), one per line; # starts a comment."""
    path = folder / TERMS_FILE
    if not path.is_file():
        return []
    terms = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if len(line) >= 2:
            terms.append(line)
    # Longest first, so "Jan Kowalski" is replaced before "Jan".
    return sorted(set(terms), key=len, reverse=True)
