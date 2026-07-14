"""Build the address/street geocoding index from PAgis address points.

Streams PAgis layer 20 (Address Point, ~230k) directly — no raw file kept on
disk (the drive is nearly full). Output feeds the dispatch + 311 collectors'
Geocoder.

Output: data/processed/address_index.json.gz
  {
    "addr":    {"<canonical address>": [lon, lat], ...}      exact points
    "streets": {"<canonical street>": [[lon, lat, num], ...] house-number
                                                             sorted, for
                                                             interpolation}
  }

The street entries are [lon, lat, num]: the third element lets the current
Geocoder interpolate a house number's position along the street, while older
code that only reads [0]/[1] still averages correctly (backward compatible).

Usage:
  python build_addr_index.py            # stream from PAgis
  python build_addr_index.py --raw FILE # build from a saved geojson instead
"""
import argparse
import gzip
import json
import sys
import time
from collections import defaultdict

import requests

from common.settings import PROCESSED_DIR
from addr_norm import norm, canon_addr, street_variants

URL = "https://www.pagis.org/arcgis/rest/services/MAPS/BaseMap/MapServer/20/query"
FIELDS = "HOUSENUM,PREFIX,STREETNAME,STREETTYPE,SUFFIX,ADDRESS"
PAGE = 1000
MAX_STREET_PTS = 1500          # cap per street (thin evenly, keep number order)


def stream_pagis():
    s = requests.Session()
    offset = 0
    while True:
        params = {"where": "HOUSENUM>0", "outFields": FIELDS, "returnGeometry": "true",
                  "outSR": "4326", "f": "json", "resultRecordCount": PAGE,
                  "resultOffset": offset, "orderByFields": "OBJECTID"}
        for attempt in range(6):
            try:
                r = s.get(URL, params=params, timeout=180)
                r.raise_for_status()
                d = r.json()
                if "error" in d:
                    raise RuntimeError(d["error"])
                break
            except Exception as e:
                if attempt == 5:
                    raise
                print(f"  page @{offset} retry {attempt + 1}: {e}", file=sys.stderr)
                time.sleep(5 * (attempt + 1))
        feats = d.get("features", [])
        if not feats:
            break
        for f in feats:
            a = f.get("attributes", {})
            g = f.get("geometry") or {}
            if g.get("x") is None:
                continue
            yield a, round(g["x"], 6), round(g["y"], 6)
        if len(feats) < PAGE or not d.get("exceededTransferLimit"):
            break
        offset += PAGE
        if offset % 20000 == 0:
            print(f"  …{offset} points")


def iter_raw(path):
    src = json.loads(open(path, encoding="utf-8").read())
    for f in src.get("features", []):
        g = f.get("geometry")
        if not g or g.get("type") != "Point":
            continue
        yield f.get("properties", {}), round(g["coordinates"][0], 6), round(g["coordinates"][1], 6)


def build(rows):
    addr = {}
    streets = defaultdict(list)
    n = 0
    for p, lon, lat in rows:
        n += 1
        name = norm(p.get("STREETNAME"))
        if not name:
            continue
        num = p.get("HOUSENUM")
        variants = street_variants(p.get("PREFIX"), p.get("STREETNAME"),
                                   p.get("STREETTYPE"), p.get("SUFFIX"))
        try:
            num_i = int(num)
        except (TypeError, ValueError):
            num_i = None
        if num_i:
            for v in variants:
                key = f"{num_i} {v}"
                addr.setdefault(key, [lon, lat])
                streets[v].append((lon, lat, num_i))
        label = canon_addr(p.get("ADDRESS"))
        if label:
            addr.setdefault(label, [lon, lat])
    print(f"{n} address points")

    streets_out = {}
    for k, pts in streets.items():
        pts = sorted(set(pts), key=lambda t: t[2])          # unique, by house number
        if len(pts) > MAX_STREET_PTS:
            step = len(pts) // MAX_STREET_PTS + 1
            pts = pts[::step]
        streets_out[k] = [[lo, la, nu] for lo, la, nu in pts]
    return addr, streets_out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", help="build from a saved addresspoints geojson instead of PAgis")
    args = ap.parse_args()

    t0 = time.time()
    rows = iter_raw(args.raw) if args.raw else stream_pagis()
    addr, streets = build(rows)

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out = PROCESSED_DIR / "address_index.json.gz"
    with gzip.open(out, "wt", encoding="utf-8") as f:
        json.dump({"addr": addr, "streets": streets}, f, separators=(",", ":"))
    print(f"addr keys: {len(addr)}, streets: {len(streets)}, "
          f"file: {out.stat().st_size / 1e6:.1f} MB  ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
