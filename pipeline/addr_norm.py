"""Shared address canonicalization for geocoding (build_addr_index + collectors).

The Little Rock CAD feed spells street types and directions inconsistently with
the PAgis address points ("16105 CHENAL PKY" in the feed vs "16105 CHENAL PKWY"
in the address index). Folding both to one canonical form before matching turns
a silent miss — which used to fall back to the street centroid and drop every
call on that street onto one wrong point — into an exact hit.
"""
import re

WS = re.compile(r"\s+")
NONALNUM = re.compile(r"[^A-Z0-9 /]+")

# street-type synonyms -> the abbreviation PAgis STREETTYPE uses
SUFFIX_MAP = {
    "AV": "AVE", "AVE": "AVE", "AVEN": "AVE", "AVENUE": "AVE",
    "BLV": "BLVD", "BLVD": "BLVD", "BOULEVARD": "BLVD", "BOUL": "BLVD",
    "BND": "BND", "BEND": "BND",
    "CIR": "CIR", "CIRC": "CIR", "CIRCLE": "CIR", "CRCL": "CIR",
    "CT": "CT", "COURT": "CT", "CRT": "CT",
    "CV": "CV", "COVE": "CV",
    "DR": "DR", "DRV": "DR", "DRIVE": "DR",
    "EXPY": "EXPY", "EXPWY": "EXPY", "EXPRESSWAY": "EXPY", "EXPRWY": "EXPY",
    "HWY": "HWY", "HIWAY": "HWY", "HIGHWAY": "HWY", "HWAY": "HWY",
    "LN": "LN", "LANE": "LN",
    "LOOP": "LOOP",
    "MNR": "MNR", "MANOR": "MNR",
    "PASS": "PASS",
    "PATH": "PATH",
    "PIKE": "PIKE",
    "PKWY": "PKWY", "PKY": "PKWY", "PKWAY": "PKWY", "PARKWAY": "PKWY", "PWY": "PKWY",
    "PL": "PL", "PLACE": "PL",
    "PLZ": "PLZ", "PLAZA": "PLZ",
    "PT": "PT", "POINT": "PT", "POINTE": "PT",
    "RD": "RD", "ROAD": "RD",
    "RUN": "RUN",
    "SQ": "SQ", "SQUARE": "SQ",
    "ST": "ST", "STR": "ST", "STREET": "ST",
    "TER": "TER", "TERR": "TER", "TERRACE": "TER",
    "TRL": "TRL", "TR": "TRL", "TRAIL": "TRL",
    "WAY": "WAY", "WY": "WAY",
    "XING": "XING", "CROSSING": "XING",
}
# leading/embedded compass directions -> single letter
DIR_MAP = {"NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W",
           "NORTHEAST": "NE", "NORTHWEST": "NW", "SOUTHEAST": "SE", "SOUTHWEST": "SW",
           "N": "N", "S": "S", "E": "E", "W": "W", "NE": "NE", "NW": "NW",
           "SE": "SE", "SW": "SW"}


def norm(s):
    """Uppercase, strip punctuation (keep the '/' that marks intersections)."""
    return WS.sub(" ", NONALNUM.sub(" ", str(s or "").upper())).strip()


def _canon_tokens(toks):
    out = []
    n = len(toks)
    for i, t in enumerate(toks):
        if t in DIR_MAP and 0 < i < n - 1:
            # a direction between the house number and the street type/name
            out.append(DIR_MAP[t])
        elif t in DIR_MAP and i == 1 and toks[0].isdigit():
            out.append(DIR_MAP[t])
        elif t in SUFFIX_MAP and i == n - 1 and i > 0:
            out.append(SUFFIX_MAP[t])
        else:
            out.append(t)
    return out


def canon_addr(s):
    """Canonicalize a full address or a street name (folds type + direction)."""
    q = norm(s)
    if "/" in q:                                   # intersection: canon each side
        a, _, b = q.partition("/")
        return canon_addr(a) + " / " + canon_addr(b)
    return " ".join(_canon_tokens(q.split()))


def street_variants(pre, name, typ, suf):
    """Canonical street keys an address point should be reachable under."""
    pre, name, typ, suf = (norm(x) for x in (pre, name, typ, suf))
    typ = SUFFIX_MAP.get(typ, typ)
    pre = DIR_MAP.get(pre, pre)
    full = " ".join(t for t in (pre, name, typ, suf) if t)
    noty = " ".join(t for t in (pre, name) if t)
    return {v for v in (full, noty, " ".join(t for t in (name, typ) if t), name) if v}
