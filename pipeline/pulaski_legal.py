"""Assessor legal-description -> PulaskiDeeds property-search terms.

PulaskiDeeds' property (legal) search matches its SUB field as a
case-insensitive prefix against its own controlled subdivision vocabulary
(6.8k options in pulaski_subdivisions.json). The assessor parcel roll uses a
different spelling of the same names ("ST CHARLES ADDN" vs the clerk's
"ST CHARLES ADN"), so a raw assessor subdivision usually returns nothing.

`SubResolver` bridges the two: it strips addition/phase/lot noise to a base
name, then maps that base to the SHORTEST clerk SUB sharing it (a prefix that
still catches the longer phased siblings, e.g. "ST CHARLES ADN" also matches
"ST CHARLES ADN PH VIII"). Unresolved names fall back to the bare base as a
prefix. Verified live: ST CHARLES ADN + LOT 373 returns 18 Toulouse Ct's full
deed/mortgage history.
"""
import json
import re
from collections import defaultdict
from pathlib import Path

WS = re.compile(r"\s+")
STOP = {"ADN", "ADDN", "ADDITION", "SUB", "SUBD", "SUBDIVISION", "REPLAT",
        "REPL", "RPLT", "PLAT", "PH", "PHASE", "AMENDED", "AMD", "REV",
        "REVISED", "TRACT", "TR", "PT", "PART", "HPR"}
ROMAN = re.compile(r"^(?=[IVX])I{0,3}(?:V|X)?I{0,3}$")
LOTMARK = re.compile(r"^(?:L\d+[A-Z]?|BLK\d+|\d+[A-Z]?)$")


def norm(s):
    return WS.sub(" ", re.sub(r"[^A-Z0-9 ]+", " ", str(s or "").upper())).strip()


def base_name(subdiv_norm):
    """Strip addition/phase/lot noise down to the stable core name."""
    toks = [t for t in subdiv_norm.split(" ") if t and t not in STOP]
    while len(toks) > 1 and (toks[-1].isdigit() or ROMAN.match(toks[-1])
                             or LOTMARK.match(toks[-1])
                             or (len(toks[-1]) == 1 and len(toks) > 2)):
        toks.pop()
    return " ".join(toks) if toks else subdiv_norm


class SubResolver:
    def __init__(self, vocab_path=None):
        if vocab_path is None:
            vocab_path = Path(__file__).parent / "pulaski_subdivisions.json"
        subs = json.loads(Path(vocab_path).read_text(encoding="utf-8"))
        by_base = defaultdict(list)
        for su in subs:
            by_base[base_name(norm(su))].append(su)
        # shortest clerk SUB per base = broadest safe prefix
        self.by_base = {b: min(v, key=len) for b, v in by_base.items()}

    def resolve(self, assessor_subdiv):
        """-> clerk SUB search prefix, or '' if the subdivision is unusable."""
        b = base_name(norm(assessor_subdiv))
        if not b:
            return ""
        if b in self.by_base:
            return self.by_base[b]
        # progressively shorten until a known clerk base matches
        toks = b.split(" ")
        while len(toks) > 1:
            toks.pop()
            cand = " ".join(toks)
            if cand in self.by_base:
                return self.by_base[cand]
        return b  # bare prefix fallback (may under-match, never wrong-family)
