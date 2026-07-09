"""Build a compact, searchable vehicle index for the web app.

The per-building `veh` strings in buildings_final.pkl (written by enrich_pp.py)
are the only vehicle-level data kept on disk — the raw personal-property dumps
are deleted after the pipeline runs. Each string is "YEAR MAKE Model" entries
joined by "; ", capped at VEH_LIST_MAX=6 per address with a trailing "+N more".
Makes are stored UPPERCASE, so the first token of each entry is the make and the
remainder is the model.

This script parses those strings, attaches each vehicle to its building's
representative point, interns makes/models/cities, and writes a flat searchable
table the browser filters client-side.

Input:  data/processed/buildings_final.pkl
Output: web/data/vehicles.json
"""
import json
import re
import time
from pathlib import Path

import pandas as pd
import shapely

ROOT = Path(r"D:\Claude Code Projects\Building_Map")
OUT = ROOT / "web" / "data"
OUT.mkdir(parents=True, exist_ok=True)

YEAR_RE = re.compile(r"^(\d{4})\s+(.*)$")
# enrich_pp caps the joined veh string at 180 chars, which can cut "+N more" to
# "+N", "+N m", "+N mo", ... — tolerate any truncation of the "more" tail and keep
# the count N so it still lands in `hidden`.
MORE_RE = re.compile(r"^\+(\d+)(\s+mo\w*)?$", re.I)
VEH_STR_CAP = 180        # length enrich_pp.veh_list slices to; tail may be cut

# Reproduce enrich_pp's address/city normalization EXACTLY so the dedup key equals
# the (address, city) key it aggregated vehicles on. Otherwise apartment footprints
# ("1 SHELBY RD APT 506" / "APT 605" / ...) keep distinct keys and the identical
# whole-building vehicle list is re-emitted at several pins.
UNIT_RE = re.compile(r"\b(APT|UNIT|STE|SUITE|LOT|BLDG|RM|TRLR|#)\b.*$")
NONALNUM = re.compile(r"[^A-Z0-9 ]+")
WS = re.compile(r"\s+")
CITY_MAP = {
    "N LITTLE ROCK": "NORTH LITTLE ROCK", "NLR": "NORTH LITTLE ROCK",
    "NO LITTLE ROCK": "NORTH LITTLE ROCK", "N LITTLE RO": "NORTH LITTLE ROCK",
    "LR": "LITTLE ROCK", "JAX": "JACKSONVILLE", "LITTLEROCK": "LITTLE ROCK",
}


def norm_addr(a):
    a = UNIT_RE.sub("", NONALNUM.sub(" ", str(a).upper()))
    return WS.sub(" ", a).strip()


def norm_city(c):
    c = WS.sub(" ", NONALNUM.sub(" ", str(c).upper())).strip()
    return CITY_MAP.get(c, c)


def nkey(a, c):
    return norm_addr(a) + "|" + norm_city(c)


t0 = time.time()
b = pd.read_pickle(ROOT / "data" / "processed" / "buildings_final.pkl")
sub = b[(b.get("nveh", 0) > 0) & (b["veh"].astype(str).str.len() > 0)].copy()
print(f"{len(sub)} building footprints carry vehicle strings", flush=True)

# Assessor attributes (incl. the vehicle string) are parcel-level: every footprint
# on a parcel inherits the same list, and enrich_pp aggregates vehicles PER ADDRESS.
# Indexing every footprint would count each vehicle once per structure and stack
# duplicate pins on one address. Keep one representative footprint per address —
# the main building, else the largest — so each vehicle is placed exactly once.
sub["_nk"] = [nkey(a, c) for a, c in zip(sub["addr"].fillna(""), sub["city"].fillna(""))]
sub = (sub.sort_values(["main", "fpa"], ascending=[False, False])
          .drop_duplicates("_nk", keep="first"))
print(f"{len(sub)} distinct addresses after parcel/footprint dedup", flush=True)

# point_on_surface() (a.k.a. representative point) guarantees a coordinate inside
# the footprint (unlike a centroid, which can fall outside a concave/multipart
# polygon)
pts = shapely.point_on_surface(sub.geometry.values)
lons = shapely.get_x(pts)
lats = shapely.get_y(pts)

make_ids, model_ids, city_ids = {}, {}, {}


def intern(d, s):
    i = d.get(s)
    if i is None:
        i = d[s] = len(d)
    return i


loc = []          # [lon, lat, addr, cityIdx] per building
veh = []          # [locIdx, year, makeIdx, modelIdx] per captured vehicle
hidden = 0        # vehicles behind "+N more" we can't place individually

addrs = sub["addr"].fillna("").astype(str).tolist()
cities = sub["city"].fillna("").astype(str).tolist()
vehs = sub["veh"].astype(str).tolist()

for lon, lat, addr, city, vstr in zip(lons, lats, addrs, cities, vehs):
    truncated = len(vstr) >= VEH_STR_CAP     # the final entry may be cut mid-token
    entries = [e.strip() for e in vstr.split(";")]
    last = len(entries) - 1
    parsed = []
    for j, e in enumerate(entries):
        if not e:
            continue
        m = MORE_RE.match(e)
        if m:
            hidden += int(m.group(1))
            continue
        if truncated and j == last:
            continue                         # truncated trailing entry -> unreliable
        ym = YEAR_RE.match(e)
        year, rest = (int(ym.group(1)), ym.group(2).strip()) if ym else (0, e)
        if not rest:
            continue
        toks = rest.split()
        make = toks[0]
        model = " ".join(toks[1:])
        parsed.append((year, make, model))
    if not parsed:
        continue
    li = len(loc)
    loc.append([round(float(lon), 5), round(float(lat), 5), addr,
                intern(city_ids, city)])
    for year, make, model in parsed:
        veh.append([li, year, intern(make_ids, make), intern(model_ids, model)])

# emit intern tables in id order
makes = [None] * len(make_ids)
for s, i in make_ids.items():
    makes[i] = s
models = [None] * len(model_ids)
for s, i in model_ids.items():
    models[i] = s
cities_out = [None] * len(city_ids)
for s, i in city_ids.items():
    cities_out[i] = s

# make popularity — the UI's datalist shows the most common makes first
freq = [0] * len(makes)
for _, _, mk, _ in veh:
    freq[mk] += 1
make_order = sorted(range(len(makes)), key=lambda i: -freq[i])

years = [v[1] for v in veh if v[1] > 1900]
out = {
    "generated": time.strftime("%Y-%m-%d"),
    "source": "Pulaski County Assessor personal-property export, situs-address matched",
    "makes": makes,
    "models": models,
    "cities": cities_out,
    "make_order": make_order,          # indices into makes[], most common first
    "loc": loc,
    "veh": veh,
    "stats": {
        "vehicles": len(veh),
        "locations": len(loc),
        "hidden": hidden,              # at aggregated high-count addresses
        "makes": len(makes),
        "year_min": min(years) if years else 0,
        "year_max": max(years) if years else 0,
    },
}

dst = OUT / "vehicles.json"
dst.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
mb = dst.stat().st_size / 1e6
print(f"vehicles: {len(veh)}  locations: {len(loc)}  makes: {len(makes)}  "
      f"models: {len(models)}  hidden(+N more): {hidden}", flush=True)
print(f"wrote {dst} ({mb:.1f} MB) in {time.time() - t0:.0f}s", flush=True)
