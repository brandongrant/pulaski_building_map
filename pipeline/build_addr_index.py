"""Build a compact address/street geocoding index from PAgis address points.

Input:  data/raw/addresspoints.geojson
Output: data/processed/address_index.json.gz  {addr: {...}, streets: {...}}
"""
import gzip
import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(r"D:\Claude Code Projects\Building_Map")
WS = re.compile(r"\s+")


def norm(s):
    return WS.sub(" ", (s or "").upper().strip()).strip()


src = json.loads((ROOT / "data" / "raw" / "addresspoints.geojson").read_text(encoding="utf-8"))
feats = src["features"]
print(len(feats), "address points")

addr = {}
streets = defaultdict(list)
for f in feats:
    g = f.get("geometry")
    p = f.get("properties", {})
    if not g or g.get("type") != "Point":
        continue
    lon, lat = round(g["coordinates"][0], 6), round(g["coordinates"][1], 6)
    num = p.get("HOUSENUM")
    pre, name, typ, suf = (norm(p.get(k)) for k in ("PREFIX", "STREETNAME", "STREETTYPE", "SUFFIX"))
    if not name:
        continue
    street_full = norm(f"{pre} {name} {typ} {suf}")
    street_noty = norm(f"{pre} {name}")
    variants = {street_full, street_noty, norm(f"{name} {typ}"), name}
    if num:
        for v in variants:
            key = f"{int(num)} {v}"
            if key not in addr:
                addr[key] = [lon, lat]
    label = norm(p.get("ADDRESS"))
    if label and label not in addr:
        addr[label] = [lon, lat]
    for v in variants:
        streets[v].append((lon, lat))

# thin street point lists to <=400 points each
streets_out = {}
for k, pts in streets.items():
    step = max(1, len(pts) // 400)
    streets_out[k] = [list(p) for p in pts[::step]][:400]

out = ROOT / "data" / "processed" / "address_index.json.gz"
with gzip.open(out, "wt", encoding="utf-8") as f:
    json.dump({"addr": addr, "streets": streets_out}, f, separators=(",", ":"))
print(f"addr keys: {len(addr)}, streets: {len(streets_out)}, "
      f"file: {out.stat().st_size / 1e6:.1f} MB")
