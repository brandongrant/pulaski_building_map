"""Normalize the Little Rock permits CSV into a map-ready GeoJSON layer.

Input:  data/raw/lr_permits.csv  (Planning & Development Permits 2019-YTD)
Output: web/data/permits/permits.geojson + permits_meta.json

Privacy: contractor/applicant names are deliberately excluded.
"""
import json
import re
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from dispatch_collect import Geocoder  # reuse the PAgis address index matcher

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "data" / "permits"
OUT.mkdir(parents=True, exist_ok=True)

CAT_LABEL = {"new": "New construction", "add": "Addition", "rem": "Remodel/repair",
             "demo": "Demolition", "roof": "Roofing", "ele": "Electrical",
             "mec": "Mechanical", "plu": "Plumbing", "usv": "Unsafe/vacant",
             "sign": "Sign/banner", "oth": "Other"}


def s(x):
    return "" if x is None or x != x else str(x)


def classify(ptype, wc, desc):
    ptype, wc, d = s(ptype).upper(), s(wc).upper(), s(desc).upper()
    if "UNSAFE" in wc:
        return "usv"
    if "DEMOL" in d or "DEMO" in wc:
        return "demo"
    if ptype == "RTW" or re.search(r"\bRE-?ROOF|SHINGLE|ROOF REPLACE|NEW ROOF", d):
        return "roof"
    if ptype == "ELE":
        return "ele"
    if ptype == "MEC":
        return "mec"
    if ptype == "PLU":
        return "plu"
    if ptype in ("SDG", "ANT") or "SIGN" in d or "BANNER" in d:
        return "sign"
    if ptype in ("BLD", "GLA"):
        if wc == "NEW":
            return "new"
        if wc == "ADDITION":
            return "add"
        if wc in ("ALTERATION", "REPAIR", "ACCESSORY"):
            return "rem"
    return "oth"


def num(s):
    try:
        return int(float(str(s).replace(",", "")))
    except (ValueError, TypeError):
        return 0


WS = re.compile(r"\s+")


def norm_addr(s):
    return WS.sub(" ", re.sub(r"[^A-Z0-9 ]+", " ", str(s).upper())).strip()


print("reading csv...", flush=True)
df = pd.read_csv(ROOT / "data" / "raw" / "lr_permits.csv", dtype=str,
                 encoding="utf-8-sig", low_memory=False)
df = df.drop_duplicates("Permit Number", keep="first")
df = df[~df["Permit Status"].isin(["Void", "Deleted"])]
print(f"{len(df)} permits after dedupe/void filter")

issue = pd.to_datetime(df["Permit Issue Date"], format="%Y %b %d %I:%M:%S %p", errors="coerce")
appl = pd.to_datetime(df["Application Date"], format="%Y %b %d %I:%M:%S %p", errors="coerce")
date = issue.fillna(appl)
df = df[date.notna()]
date = date[date.notna()]

geo = Geocoder(ROOT / "data" / "processed" / "address_index.json.gz")
STATUS = {"Open": "O", "Closed": "C", "Stop Work": "W"}

feats = []
fails = 0
t0 = time.time()
for (_, r), dt in zip(df.iterrows(), date):
    addr = s(r["Property Address"]).strip()
    if not addr:
        fails += 1
        continue
    lon, lat, quality = geo.geocode(addr)
    if lon is None:
        fails += 1
        continue
    cat = classify(r["Permit Type"], r["Work Class Mapped"], r["Project Description"])
    desc = s(r["Project Description"])[:70].strip()
    p = {"n": s(r["Permit Number"]), "t": cat, "d": int(dt.strftime("%Y%m%d")),
         "s": STATUS.get(s(r["Permit Status"]), "?"), "a": norm_addr(addr), "j": "LR"}
    v = num(r["Declared Value of Project"])
    if v:
        p["v"] = v
    sf = num(r["Square Feet"])
    if sf:
        p["sf"] = sf
    if desc:
        p["ds"] = desc
    if quality != "exact_address":
        p["gq"] = quality
    feats.append({"type": "Feature",
                  "geometry": {"type": "Point", "coordinates": [lon, lat]},
                  "properties": p})

print(f"geocoded {len(feats)} ({fails} dropped) in {time.time() - t0:.0f}s")
(OUT / "permits.geojson").write_text(
    json.dumps({"type": "FeatureCollection", "features": feats}), encoding="utf-8")

cats = {}
for f in feats:
    cats[f["properties"]["t"]] = cats.get(f["properties"]["t"], 0) + 1
meta = {
    "generated": time.strftime("%Y-%m-%d"),
    "source": "City of Little Rock Planning & Development Permits (2019-YTD)",
    "count": len(feats),
    "date_min": int(date.min().strftime("%Y%m%d")),
    "date_max": int(date.max().strftime("%Y%m%d")),
    "geocode_rate": round(len(feats) / max(1, len(feats) + fails), 3),
    "cats": cats,
    "labels": CAT_LABEL,
}
(OUT / "permits_meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
sz = (OUT / "permits.geojson").stat().st_size / 1e6
print(f"wrote permits.geojson ({sz:.1f} MB), meta: {json.dumps(cats)}")
