"""Short-form 3D ULPIN: ``STATE-CITY-BUILDING-FLOOR-UNIT``.

For example ``WB-KOL-B001-F03-U301`` — a unit on the third floor of the first
registered building in Kolkata, West Bengal.

Relationship to :mod:`app.ulpin.codec`
--------------------------------------
This module and ``codec`` produce two identifiers for the same volume, and both
are stored:

``codec``      ``IN29BLR0001234-A1-F007-U0412-Y``
    The *canonical* identifier. It carries the 14-character parcel ULPIN
    unmodified, so an existing 2D cadastral system can slice off the first 14
    characters and still resolve the parcel. It is machine-facing, and it is
    what lineage, supersession and interchange use.

``shortcode``  ``WB-KOL-B001-F03-U301``
    The *public* identifier. It is what a citizen reads off a notice, quotes on
    the phone and types into the search box. It is short, pronounceable, and
    carries no check character, because a human reading digits aloud is not the
    error model a check character defends against.

Neither is derivable from the other — the short form deliberately discards the
survey lineage — so both are persisted on the ``ulpins`` row and both are
uniquely indexed. "No duplicate ULPINs" has to hold for the form people
actually quote, not only the form the database prefers.

Everything here is pure: no I/O, no database, no clock. Sequence allocation —
the part that genuinely needs a transaction — lives in
:mod:`app.services.ulpin_service`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Final

# ===========================================================================
# Jurisdiction codes
# ===========================================================================
# The two-letter codes India already uses on number plates and in land records.
# Keyed by the upper-cased state name with punctuation stripped, so "Jammu &
# Kashmir", "JAMMU AND KASHMIR" and "jammu kashmir" all land on the same entry.
STATE_CODES: Final[dict[str, str]] = {
    "ANDAMAN AND NICOBAR ISLANDS": "AN",
    "ANDHRA PRADESH": "AP",
    "ARUNACHAL PRADESH": "AR",
    "ASSAM": "AS",
    "BIHAR": "BR",
    "CHANDIGARH": "CH",
    "CHHATTISGARH": "CG",
    "DADRA AND NAGAR HAVELI AND DAMAN AND DIU": "DN",
    "DELHI": "DL",
    "GOA": "GA",
    "GUJARAT": "GJ",
    "HARYANA": "HR",
    "HIMACHAL PRADESH": "HP",
    "JAMMU AND KASHMIR": "JK",
    "JHARKHAND": "JH",
    "KARNATAKA": "KA",
    "KERALA": "KL",
    "LADAKH": "LA",
    "LAKSHADWEEP": "LD",
    "MADHYA PRADESH": "MP",
    "MAHARASHTRA": "MH",
    "MANIPUR": "MN",
    "MEGHALAYA": "ML",
    "MIZORAM": "MZ",
    "NAGALAND": "NL",
    "ODISHA": "OD",
    "PUDUCHERRY": "PY",
    "PUNJAB": "PB",
    "RAJASTHAN": "RJ",
    "SIKKIM": "SK",
    "TAMIL NADU": "TN",
    "TELANGANA": "TS",
    "TRIPURA": "TR",
    "UTTAR PRADESH": "UP",
    "UTTARAKHAND": "UK",
    "WEST BENGAL": "WB",
}

# Historical and colloquial names still in circulation in property documents.
STATE_ALIASES: Final[dict[str, str]] = {
    "NEW DELHI": "DL",
    "NCT OF DELHI": "DL",
    "ORISSA": "OD",
    "PONDICHERRY": "PY",
    "UTTARANCHAL": "UK",
    "JAMMU KASHMIR": "JK",
    "ANDAMAN NICOBAR": "AN",
    "TAMILNADU": "TN",
}

# Three-letter codes for cities whose conventional abbreviation is not simply
# the first three letters of the name. Anything not listed falls back to
# :func:`derive_city_code`, which is deterministic — so an unlisted city still
# gets a stable code, just a less familiar one.
CITY_CODES: Final[dict[str, str]] = {
    "KOLKATA": "KOL",
    "CALCUTTA": "KOL",
    "MUMBAI": "MUM",
    "BOMBAY": "MUM",
    "BENGALURU": "BLR",
    "BANGALORE": "BLR",
    "CHENNAI": "CHN",
    "MADRAS": "CHN",
    "HYDERABAD": "HYD",
    "NEW DELHI": "DEL",
    "DELHI": "DEL",
    "PUNE": "PUN",
    "POONA": "PUN",
    "AHMEDABAD": "AMD",
    "SURAT": "SUR",
    "JAIPUR": "JAI",
    "LUCKNOW": "LKO",
    "KANPUR": "KNP",
    "NAGPUR": "NGP",
    "INDORE": "IDR",
    "THANE": "THN",
    "BHOPAL": "BPL",
    "VISAKHAPATNAM": "VSKP"[:3],
    "PATNA": "PAT",
    "VADODARA": "VDR",
    "GHAZIABAD": "GZB",
    "LUDHIANA": "LDH",
    "AGRA": "AGR",
    "NASHIK": "NSK",
    "FARIDABAD": "FBD",
    "MEERUT": "MRT",
    "RAJKOT": "RJT",
    "VARANASI": "VNS",
    "SRINAGAR": "SXR",
    "AURANGABAD": "ABD",
    "DHANBAD": "DHN",
    "AMRITSAR": "ASR",
    "NAVI MUMBAI": "NVM",
    "PRAYAGRAJ": "PRY",
    "ALLAHABAD": "PRY",
    "RANCHI": "RNC",
    "HOWRAH": "HWH",
    "COIMBATORE": "CBE",
    "JABALPUR": "JBP",
    "GWALIOR": "GWL",
    "VIJAYAWADA": "VJA",
    "JODHPUR": "JDH",
    "MADURAI": "MDU",
    "RAIPUR": "RPR",
    "KOTA": "KTA",
    "CHANDIGARH": "CHD",
    "GURUGRAM": "GGN",
    "GURGAON": "GGN",
    "NOIDA": "NOI",
    "THIRUVANANTHAPURAM": "TVM",
    "KOCHI": "COK",
    "COCHIN": "COK",
    "GUWAHATI": "GAU",
    "BHUBANESWAR": "BBS",
    "DEHRADUN": "DDN",
    "MYSURU": "MYS",
    "MYSORE": "MYS",
    "PANAJI": "PNJ",
    "SHIMLA": "SML",
    "IMPHAL": "IMF",
    "AIZAWL": "AJL",
    "SHILLONG": "SHL",
    "KOHIMA": "KOH",
    "AGARTALA": "IXA",
    "GANGTOK": "GTK",
    "ITANAGAR": "ITN",
    "PORT BLAIR": "IXZ",
    "LEH": "IXL",
    "KAVARATTI": "KVR",
    "DAMAN": "DAM",
    "SILVASSA": "SLV",
}

# ===========================================================================
# Storey classes
# ===========================================================================
# The leading letter of the floor segment. It is not decoration: without it,
# basement 3 and floor 3 are the same three characters, and the register would
# be unable to tell a parking level from a flat.
STOREY_CLASS: Final[dict[str, str]] = {
    "BASEMENT": "B",
    "GROUND": "G",
    "MEZZANINE": "M",
    "UPPER": "F",
    "TERRACE": "T",
    "AIR_RIGHTS": "A",
    "STILT": "S",
    "PODIUM": "P",
}
CLASS_TO_FLOOR_TYPE: Final[dict[str, str]] = {v: k for k, v in STOREY_CLASS.items()}

# ===========================================================================
# Grammar
# ===========================================================================
# Two digits is the canonical width and matches the specified example (F03).
# Three is permitted so that a genuine 100-storey tower is representable rather
# than silently truncated; the canonical form always uses the narrower width
# that fits, so one floor has exactly one spelling.
SEGMENT_SEPARATOR: Final[str] = "-"

_STATE_RE = r"[A-Z]{2}"
_CITY_RE = r"[A-Z]{3}"
_BUILDING_RE = r"B\d{3,4}"
_FLOOR_RE = r"[BGMFTASP]\d{2,3}"
_UNIT_RE = r"U\d{3,4}"

SHORTCODE_PATTERN: Final[re.Pattern[str]] = re.compile(
    rf"^(?P<state>{_STATE_RE})-(?P<city>{_CITY_RE})-(?P<building>{_BUILDING_RE})"
    rf"-(?P<floor>{_FLOOR_RE})-(?P<unit>{_UNIT_RE})$"
)

# A building-level short code stops after the building segment: WB-KOL-B001.
BUILDING_SHORTCODE_PATTERN: Final[re.Pattern[str]] = re.compile(
    rf"^(?P<state>{_STATE_RE})-(?P<city>{_CITY_RE})-(?P<building>{_BUILDING_RE})$"
)

MAX_BUILDING_SEQUENCE: Final[int] = 9999
MAX_UNIT_SEQUENCE: Final[int] = 9999
MIN_FLOOR: Final[int] = -99
MAX_FLOOR: Final[int] = 999


class ShortCodeError(ValueError):
    """Raised when a short code cannot be built or cannot be parsed.

    A ``ValueError`` subclass so callers that only care that the input was bad
    can catch the builtin, while the API layer catches this specifically and
    turns it into a 422 rather than a 500.
    """


# ===========================================================================
# Normalisation helpers
# ===========================================================================
def _fold(name: str) -> str:
    """Upper-case, strip accents, and reduce punctuation to single spaces.

    ``Puducherry``, ``puducherry`` and ``Pondicherry `` must not be three
    different places, and a stray ampersand in a state name copied out of a
    scanned deed must not create a fourth.
    """
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    upper = ascii_only.upper()
    # "&" reads as "AND" in every official name that contains it.
    upper = upper.replace("&", " AND ")
    cleaned = re.sub(r"[^A-Z0-9]+", " ", upper)
    return " ".join(cleaned.split())


def state_code(state: str) -> str:
    """Two-letter code for a state name, or the code itself if already given.

    >>> state_code("West Bengal")
    'WB'
    >>> state_code("wb")
    'WB'
    """
    folded = _fold(state)
    if not folded:
        raise ShortCodeError("State is required to build a ULPIN")

    # Already a code — accept it, but only if it is one we actually issue.
    if len(folded) == 2 and folded in set(STATE_CODES.values()):
        return folded

    if folded in STATE_CODES:
        return STATE_CODES[folded]
    if folded in STATE_ALIASES:
        return STATE_ALIASES[folded]

    # Tolerate a trailing "STATE"/"UT" that some address forms append.
    trimmed = re.sub(r"\s+(STATE|UT|UNION TERRITORY)$", "", folded)
    if trimmed in STATE_CODES:
        return STATE_CODES[trimmed]

    raise ShortCodeError(
        f"{state!r} is not a recognised Indian state or union territory. "
        "Supply one of its official names, or its two-letter code."
    )


def derive_city_code(city: str) -> str:
    """Fallback three-letter code for a city that is not in :data:`CITY_CODES`.

    Deterministic by construction, because a code that changed between runs
    would let the same building be registered twice under different
    identifiers. The rule: first letter, then the next two letters that are not
    vowels, padded from the remaining letters if the name is vowel-heavy.
    """
    letters = [ch for ch in _fold(city) if ch.isalpha()]
    if not letters:
        raise ShortCodeError("City is required to build a ULPIN")
    if len(letters) < 3:
        # "Goa" style short names: pad with X so the segment is always 3 wide.
        return ("".join(letters) + "XX")[:3]

    head = letters[0]
    consonants = [ch for ch in letters[1:] if ch not in "AEIOU"]
    tail = consonants[:2]
    if len(tail) < 2:
        # Fill from whatever is left, preserving order, skipping the ones used.
        for ch in letters[1:]:
            if ch not in tail:
                tail.append(ch)
            if len(tail) == 2:
                break
    return (head + "".join(tail) + "XX")[:3]


def city_code(city: str) -> str:
    """Three-letter code for a city name, or the code itself if already given.

    >>> city_code("Kolkata")
    'KOL'
    >>> city_code("Calcutta")
    'KOL'
    """
    folded = _fold(city)
    if not folded:
        raise ShortCodeError("City is required to build a ULPIN")
    if folded in CITY_CODES:
        return CITY_CODES[folded]
    if len(folded) == 3 and folded.isalpha():
        return folded
    return derive_city_code(folded)


# ===========================================================================
# Segment builders
# ===========================================================================
def building_segment(sequence: int) -> str:
    """``B001`` for the first building registered in a city.

    The sequence is per (state, city) — not global and not per parcel — because
    that is the scope a citizen can hold in their head: "building 1 in Kolkata".
    """
    if not 1 <= sequence <= MAX_BUILDING_SEQUENCE:
        raise ShortCodeError(
            f"Building sequence {sequence} is outside 1..{MAX_BUILDING_SEQUENCE}"
        )
    width = 3 if sequence <= 999 else 4
    return f"B{sequence:0{width}d}"


def floor_segment(floor_number: int, floor_type: str = "UPPER") -> str:
    """``F03`` for the third floor, ``B01`` for the first basement.

    Follows the sanctioned plan, so a tower that omits floor 13 leaves a real
    gap between ``F12`` and ``F14``. The gap is information: it says the plan
    has no such storey, which is exactly what an auditor needs to know.
    """
    if not MIN_FLOOR <= floor_number <= MAX_FLOOR:
        raise ShortCodeError(
            f"Floor {floor_number} is outside {MIN_FLOOR}..{MAX_FLOOR}"
        )

    key = _fold(floor_type).replace(" ", "_") or "UPPER"
    if key not in STOREY_CLASS:
        raise ShortCodeError(
            f"{floor_type!r} is not a storey class. "
            f"Expected one of: {', '.join(sorted(STOREY_CLASS))}"
        )

    # A negative number always means a basement whatever the caller declared,
    # and the ground floor is 0 whatever it is called locally.
    if floor_number < 0:
        key = "BASEMENT"
    elif floor_number == 0 and key == "UPPER":
        key = "GROUND"

    magnitude = abs(floor_number)
    width = 2 if magnitude <= 99 else 3
    return f"{STOREY_CLASS[key]}{magnitude:0{width}d}"


def unit_segment(sequence: int) -> str:
    """``U301`` — conventionally floor-major (3rd floor, 1st unit) but the
    generator does not require that; it only requires uniqueness on the floor.
    """
    if not 0 <= sequence <= MAX_UNIT_SEQUENCE:
        raise ShortCodeError(f"Unit sequence {sequence} is outside 0..{MAX_UNIT_SEQUENCE}")
    width = 3 if sequence <= 999 else 4
    return f"U{sequence:0{width}d}"


def unit_sequence_for(floor_number: int, ordinal: int, unit_number: str | None = None) -> int:
    """Choose the numeric part of the unit segment.

    Preference order, and the reason for it:

    1. ``unit_number`` when it is already numeric and in range — flat 301 on the
       third floor should be ``U301``, because that is the number on the door
       and any other choice makes the identifier feel arbitrary.
    2. Otherwise floor-major from the ordinal — ``floor × 100 + n`` — which
       reproduces the same convention for doors labelled ``3A``, ``3B``.
    3. Otherwise the bare ordinal, for basements and odd storeys where
       floor-major would overflow.
    """
    if unit_number is not None:
        digits = unit_number.strip()
        if digits.isdigit():
            value = int(digits)
            if 0 <= value <= MAX_UNIT_SEQUENCE:
                return value

    if ordinal < 1:
        raise ShortCodeError(f"Unit ordinal must be 1 or greater, got {ordinal}")

    if 0 <= floor_number <= 98 and ordinal <= 99:
        return floor_number * 100 + ordinal
    return ordinal


# ===========================================================================
# Compose / parse
# ===========================================================================
@dataclass(frozen=True, slots=True)
class ShortCodeParts:
    """A parsed short code. Frozen because an identifier is not a mutable thing."""

    state: str
    city: str
    building: str
    floor: str
    unit: str

    @property
    def code(self) -> str:
        return SEGMENT_SEPARATOR.join(
            (self.state, self.city, self.building, self.floor, self.unit)
        )

    @property
    def building_code(self) -> str:
        """The building-level identifier this unit belongs to: ``WB-KOL-B001``."""
        return SEGMENT_SEPARATOR.join((self.state, self.city, self.building))

    @property
    def building_sequence(self) -> int:
        return int(self.building[1:])

    @property
    def unit_sequence(self) -> int:
        return int(self.unit[1:])

    @property
    def floor_number(self) -> int:
        magnitude = int(self.floor[1:])
        return -magnitude if self.floor[0] == "B" else magnitude

    @property
    def floor_type(self) -> str:
        return CLASS_TO_FLOOR_TYPE.get(self.floor[0], "UPPER")

    def __str__(self) -> str:
        return self.code


def compose(
    *,
    state: str,
    city: str,
    building_sequence: int,
    floor_number: int,
    unit_sequence: int,
    floor_type: str = "UPPER",
) -> str:
    """Build a canonical short code. Keyword-only: five same-typed arguments in
    a row is precisely the call that gets silently transposed.

    >>> compose(state="West Bengal", city="Kolkata", building_sequence=1,
    ...         floor_number=3, unit_sequence=301)
    'WB-KOL-B001-F03-U301'
    """
    return SEGMENT_SEPARATOR.join(
        (
            state_code(state),
            city_code(city),
            building_segment(building_sequence),
            floor_segment(floor_number, floor_type),
            unit_segment(unit_sequence),
        )
    )


def compose_building(*, state: str, city: str, building_sequence: int) -> str:
    """The building-level short code: ``WB-KOL-B001``."""
    return SEGMENT_SEPARATOR.join(
        (state_code(state), city_code(city), building_segment(building_sequence))
    )


def normalise(code: str) -> str:
    """Upper-case, trim, and accept a few spellings people actually type.

    Spaces, underscores and en-dashes all become the canonical hyphen. Someone
    reading a code off a printed notice into a search box should not be told
    their property does not exist because their keyboard produced ``–``.
    """
    if not code:
        raise ShortCodeError("ULPIN is empty")
    cleaned = code.strip().upper()
    cleaned = re.sub(r"[\s_‐-―]+", SEGMENT_SEPARATOR, cleaned)
    cleaned = re.sub(rf"{SEGMENT_SEPARATOR}{{2,}}", SEGMENT_SEPARATOR, cleaned)
    return cleaned.strip(SEGMENT_SEPARATOR)


def parse(code: str) -> ShortCodeParts:
    """Parse a unit-level short code, raising :class:`ShortCodeError` if it is
    not one."""
    cleaned = normalise(code)
    match = SHORTCODE_PATTERN.match(cleaned)
    if match is None:
        raise ShortCodeError(
            f"{code!r} is not a valid ULPIN. "
            "Expected STATE-CITY-BUILDING-FLOOR-UNIT, for example WB-KOL-B001-F03-U301."
        )
    return ShortCodeParts(**match.groupdict())


def is_valid(code: str) -> bool:
    """True when ``code`` is a well-formed unit-level short code.

    Well-formed is not the same as issued: this says the string could name a
    property, not that it does. Existence is a database question.
    """
    try:
        parse(code)
    except ShortCodeError:
        return False
    return True


def is_building_code(code: str) -> bool:
    try:
        return BUILDING_SHORTCODE_PATTERN.match(normalise(code)) is not None
    except ShortCodeError:
        return False


def looks_like_shortcode(code: str) -> bool:
    """Cheap discriminator for the search box, which accepts both forms.

    Only asks whether the string has the *shape* of a short code, so a partial
    or slightly malformed entry still routes to the right branch of the search
    and gets a useful error instead of "no results".
    """
    try:
        cleaned = normalise(code)
    except ShortCodeError:
        return False
    return bool(re.match(rf"^{_STATE_RE}-{_CITY_RE}(-|$)", cleaned))
