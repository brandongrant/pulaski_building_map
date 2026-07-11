"""Build the legal-description crosswalk for the deeds collector.

parcel_owners.pkl (pipeline/build_owner_index.py) -> deeds/legal_index.json.gz
  "SUBDIV|LOT|BLOCK" -> [lon, lat, situs addr, city]

Pulaski Deeds has no street-address search and its results carry only legal
descriptions (SUBDIVISION / LOT / BLOCK), so this index is how recorded
documents get placed on the map. Keys with multiple parcels (condo splits,
re-plats) keep the first parcel — good enough for a point marker.

Commit the output to the `data` branch as deeds/legal_index.json.gz.

The clerk's index and the assessor's parcel roll disagree on naming
("CHENAL VALLEY ADN" vs plat-level "CHENAL VALLEY MARGEUAX", "FOXWOOD SUB
PH VI A" vs "FOXWOOD PH 6A"), so three tiers are emitted:

  exact  "SUBDIV|LOT|BLOCK" and "SUBDIV|LOT|"        -> [lon, lat, addr, city]
  base   suffix/phase-stripped subdivision, same keys
  lb     "LOT|BLOCK" -> [[base_subdiv, lon, lat, addr, city], ...]
         (for unique prefix-containment matches in the collector)
"""
import gzip
import json
import re

import pandas as pd

from common.settings import PROCESSED_DIR

SRC = PROCESSED_DIR / "parcel_owners.pkl"
OUT = PROCESSED_DIR / "legal_index.json.gz"

WS = re.compile(r"\s+")
STOP = {"ADN", "ADDN", "ADDITION", "SUB", "SUBD", "SUBDIVISION", "REPLAT",
        "REPL", "RPLT", "PLAT", "PH", "PHASE", "AMENDED", "AMD", "REV",
        "REVISED", "TRACT", "TR", "PT", "PART", "HPR"}
ROMAN = re.compile(r"^(?=[IVX])I{0,3}(?:V|X)?I{0,3}$")


def norm(s):
    return WS.sub(" ", re.sub(r"[^A-Z0-9 ]+", " ", str(s or "").upper())).strip()


def base_name(subdiv_norm):
    """Strip suffix/phase noise: ADN/SUB/PH tokens anywhere, then trailing
    numbers / roman numerals / single letters (never below one token)."""
    toks = [t for t in subdiv_norm.split(" ") if t and t not in STOP]
    while len(toks) > 1 and (toks[-1].isdigit() or ROMAN.match(toks[-1])
                             or (len(toks[-1]) == 1 and len(toks) > 2)):
        toks.pop()
    return " ".join(toks) if toks else subdiv_norm


def lb_norm(v):
    return str(v or "").lstrip("0") or ""


df = pd.read_pickle(SRC)
exact, base, lb = {}, {}, {}
for r in df.itertuples():
    if not r.subdiv:
        continue
    sn = norm(r.subdiv)
    if not sn:
        continue
    bn = base_name(sn)
    lot, blk = lb_norm(r.lot), lb_norm(r.block)
    val = [r.lon, r.lat, r.addr, r.city]
    for d, name in ((exact, sn), (base, bn)):
        for k in {f"{name}|{lot}|{blk}", f"{name}|{lot}|"}:
            d.setdefault(k, val)
    if lot:
        lb.setdefault(f"{lot}|{blk}", []).append([bn] + val)

# lot|block buckets are only useful when a containment test can be decisive;
# huge buckets (lot 1 across the county) still work — the collector requires
# a UNIQUE prefix hit — but cap the pathological ones to keep the file sane
for k in list(lb):
    if len(lb[k]) > 400:
        del lb[k]

out = {"exact": exact, "base": base, "lb": lb}
with gzip.open(OUT, "wt", encoding="utf-8") as f:
    json.dump(out, f, separators=(",", ":"))
print(f"exact {len(exact)}, base {len(base)}, lot|block buckets {len(lb)} "
      f"-> {OUT} ({OUT.stat().st_size / 1e6:.1f} MB)")
