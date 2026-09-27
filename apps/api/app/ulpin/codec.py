"""The 3D ULPIN codec — pure functions, no database, no framework.

Structure::

    IN29BLR0001234 - A1 - F007 - U0412 - K
    └── parent 2D ──┘ └┬┘  └─┬─┘  └─┬─┘  └┬┘
                   block  storey  unit  check

The parent 14-character ULPIN is carried through unmodified, so every 3D
identifier still resolves to a real parcel in the existing cadastre. The suffix
is additive.

This module must stay byte-compatible with ``fn_ulpin_checksum`` /
``fn_storey_code`` in ``database/schemas/01_types.sql`` and with
``packages/ulpin-js``. The shared vectors in ``tests/fixtures/ulpin-vectors.json``
are what enforce that; CI fails if any implementation drifts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Literal

# ISO 7064 MOD 36,36 operates over this alphabet, in this order. Changing it
# changes every check character ever issued.
ALPHABET: Final[str] = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
MODULUS: Final[int] = 36

# Crockford Base32 omits I, L, O and U — the characters people misread as 1, 1,
# 0 and V. New suffix characters are minted from this set only. Validation still
# accepts the full 36, because inherited 2D codes may already contain them.
MINT_ALPHABET: Final[str] = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

PARCEL_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Z0-9]{14}$")
FULL_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?P<parcel>[A-Z0-9]{14})-"
    r"(?P<block>[A-Z0-9]{2})-"
    r"(?P<storey>[BGMFTA][0-9]{3})-"
    r"(?P<unit>U[0-9]{4})-"
    r"(?P<check>[A-Z0-9])$"
)

StoreyPrefix = Literal["B", "G", "M", "F", "T", "A"]


class ULPINFormatError(ValueError):
    """Raised for anything structurally wrong with an identifier."""


@dataclass(frozen=True, slots=True)
class ULPINParts:
    parcel: str
    block: str
    storey: str
    unit: str
    check: str

    @property
    def code(self) -> str:
        return f"{self.parcel}-{self.block}-{self.storey}-{self.unit}-{self.check}"

    @property
    def floor_number(self) -> int:
        return storey_code_to_floor(self.storey)

    @property
    def unit_ordinal(self) -> int:
        return int(self.unit[1:])


# ===========================================================================
# Check character
# ===========================================================================
def checksum(payload: str) -> str:
    """ISO 7064 MOD 36,36 hybrid check character.

    Detects every single-character substitution and every transposition of
    adjacent characters — the two mistakes people actually make when copying an
    identifier off a document.
    """
    p = MODULUS
    for char in payload.upper():
        try:
            value = ALPHABET.index(char)
        except ValueError as exc:
            raise ULPINFormatError(
                f"{char!r} is not in the ULPIN alphabet"
            ) from exc
        p = ((p + value) % MODULUS or MODULUS) * 2 % (MODULUS + 1)

    check_value = (MODULUS + 1 - p) % MODULUS
    return ALPHABET[check_value]


def verify(code: str) -> bool:
    """True when the code is well-formed and its check character agrees."""
    normalised = normalise(code)
    match = FULL_RE.match(normalised)
    if match is None:
        return False
    payload = normalised.rsplit("-", 1)[0].replace("-", "")
    return checksum(payload) == match.group("check")


# ===========================================================================
# Storey codes
# ===========================================================================
def storey_code(floor_number: int, floor_type: str = "UPPER") -> str:
    """Encode a signed floor number as a four-character storey code.

    The code follows the *sanctioned plan*, not a running count. A tower whose
    plan skips floor 13 really does have a gap between F012 and F014, and that
    gap is information: it tells a later reader the omission was intentional
    rather than a missing record.
    """
    kind = (floor_type or "UPPER").upper()

    if kind == "BASEMENT" or floor_number < 0:
        # Basements count downward from the surface: -1 is B001.
        return f"B{abs(floor_number):03d}"
    if kind == "GROUND" or floor_number == 0:
        return "G000"
    if kind == "MEZZANINE":
        return f"M{floor_number:03d}"
    if kind == "TERRACE":
        return f"T{floor_number:03d}"
    if kind == "AIR_RIGHTS":
        return f"A{floor_number:03d}"
    # STILT and PODIUM are structurally upper storeys for identifier purposes.
    return f"F{floor_number:03d}"


def storey_code_to_floor(code: str) -> int:
    """Inverse of :func:`storey_code`. Basements come back negative."""
    code = code.upper()
    if len(code) != 4 or code[0] not in "BGMFTA" or not code[1:].isdigit():
        raise ULPINFormatError(f"{code!r} is not a storey code")
    magnitude = int(code[1:])
    return -magnitude if code[0] == "B" else magnitude


def infer_floor_type(floor_number: int) -> str:
    if floor_number < 0:
        return "BASEMENT"
    if floor_number == 0:
        return "GROUND"
    return "UPPER"


# ===========================================================================
# Unit codes
# ===========================================================================
def unit_code(ordinal: int, *, floor_number: int = 0) -> str:
    """Encode a unit's within-storey ordinal as ``U`` plus four digits.

    Basements use the reserved ``U9LNN`` band: ``9``, the basement level, then
    the ordinal. Without a separate band a basement ordinal would collide with
    an upper-storey one, since the storey segment alone distinguishes ``B002``
    from ``F002`` but the unit segment is shared.

    The band caps a basement level at 99 units, which is enough for parking bays
    on one level; a level with more is split across two level codes at ingest.
    """
    if ordinal < 1:
        raise ULPINFormatError("Unit ordinals start at 1")

    if floor_number < 0:
        level = abs(floor_number)
        if level > 9:
            raise ULPINFormatError("Basement levels below -9 need a wider unit band")
        if ordinal > 99:
            raise ULPINFormatError("At most 99 units per basement level")
        return f"U9{level}{ordinal:02d}"

    if ordinal > 9999:
        raise ULPINFormatError("At most 9999 units per storey")
    return f"U{ordinal:04d}"


# ===========================================================================
# Compose / parse
# ===========================================================================
def normalise(code: str) -> str:
    """Upper-case, strip whitespace, and accept the unhyphenated form."""
    cleaned = re.sub(r"\s+", "", code).upper()
    if "-" in cleaned:
        return cleaned
    if len(cleaned) == 26:
        # 14 + 2 + 4 + 5 + 1, re-hyphenated at the segment boundaries.
        return "-".join(
            (cleaned[:14], cleaned[14:16], cleaned[16:20], cleaned[20:25], cleaned[25:])
        )
    return cleaned


def compose(
    *,
    parcel_ulpin: str,
    block_code: str,
    floor_number: int,
    unit_ordinal: int,
    floor_type: str | None = None,
) -> str:
    """Build a complete 3D ULPIN, check character included."""
    parcel = parcel_ulpin.strip().upper()
    if not PARCEL_RE.match(parcel):
        raise ULPINFormatError(
            f"{parcel!r} is not a 14-character parcel ULPIN"
        )

    block = block_code.strip().upper()
    if len(block) != 2 or not block.isalnum():
        raise ULPINFormatError(f"{block!r} is not a two-character block code")

    storey = storey_code(floor_number, floor_type or infer_floor_type(floor_number))
    unit = unit_code(unit_ordinal, floor_number=floor_number)

    payload = f"{parcel}{block}{storey}{unit}"
    return f"{parcel}-{block}-{storey}-{unit}-{checksum(payload)}"


def parse(code: str) -> ULPINParts:
    normalised = normalise(code)
    match = FULL_RE.match(normalised)
    if match is None:
        raise ULPINFormatError(f"{code!r} is not a well-formed 3D ULPIN")

    payload = normalised.rsplit("-", 1)[0].replace("-", "")
    expected = checksum(payload)
    if expected != match.group("check"):
        raise ULPINFormatError(
            f"Check character mismatch: expected {expected}, got {match.group('check')}"
        )

    return ULPINParts(
        parcel=match.group("parcel"),
        block=match.group("block"),
        storey=match.group("storey"),
        unit=match.group("unit"),
        check=match.group("check"),
    )


def is_parcel_ulpin(code: str) -> bool:
    return bool(PARCEL_RE.match(code.strip().upper()))


__all__ = [
    "ALPHABET",
    "MINT_ALPHABET",
    "ULPINFormatError",
    "ULPINParts",
    "checksum",
    "compose",
    "infer_floor_type",
    "is_parcel_ulpin",
    "normalise",
    "parse",
    "storey_code",
    "storey_code_to_floor",
    "unit_code",
    "verify",
]
