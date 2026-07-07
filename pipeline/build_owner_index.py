"""Build the owner / address search index from the PAgis Parcel layer.

Streams Parcel (layer 68) pages — attributes + generalized geometry — and
reduces each page to rows as it arrives (no raw geojson is kept on disk;
the drive is nearly full). ~181 requests x 1000 parcels.

Output:
  data/processed/parcel_owners.pkl   full per-parcel crosswalk seed
      (parcel id, owner, situs address, subdivision/lot/block, legal,
       values, owner mailing city/state, representative lon/lat)
  web/data/owners.json               compact client search index
      {"generated", "count", "cities": [...],
       "owners": [[name, [[addr, cityIdx, lon, lat, totalValue], ...]], ...]}

parcel_owners.pkl is the seed of the property_crosswalk described in
docs/recorded_documents_plan.md: SUBDIV/LOT/BLOCK are the join keys the
Pulaski Deeds property search uses (it has no street-address search).
"""
import json
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests
from shapely.geometry import shape

ROOT = Path(r"D:\Claude Code Projects\Building_Map")          # shared data/ (gitignored)
REPO = Path(__file__).resolve().parent.parent                  # this checkout's web/
OUT_PKL = ROOT / "data" / "processed" / "parcel_owners.pkl"
OUT_JSON = REPO / "web" / "data" / "owners.json"

URL = "https://www.pagis.org/arcgis/rest/services/MAPS/BaseMap/MapServer/68/query"
FIELDS = ("PARCELID,OWNERNAME,ADRLABEL,ADRCITY,SUBDIV,LOT,BLOCK,PARCELLGL,"
          "TOTALVALUE,IMPVALUE,ASSESSVAL,PARCELTYPE,OWNER_CITY,OWNER_ST")
PAGE = 1000

s = requests.Session()


def get(params, tries=6):
    last = None
    for i in range(tries):
        try:
            r = s.get(URL, params=params, timeout=180)
            r.raise_for_status()
            d = r.json()
            if "error" in d:
                raise RuntimeError(d["error"])
            return d
        except Exception as e:
            last = e
            print(f"  retry {i + 1}: {e}", flush=True)
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"gave up at offset {params.get('resultOffset')}: {last}")


def clean(v):
    return " ".join(str(v).split()) if v not in (None, "", "Null") else ""


count = get({"where": "1=1", "returnCountOnly": "true", "f": "json"})["count"]
npages = (count + PAGE - 1) // PAGE
print(f"parcels: {count} features, {npages} pages", flush=True)

rows = []
t0 = time.time()
for p in range(npages):
    d = get({
        "where": "1=1", "outFields": FIELDS, "returnGeometry": "true",
        "f": "geojson", "outSR": "4326",
        # search markers only need ~2 m accuracy; generalizing saves ~10x bytes
        "geometryPrecision": 6, "maxAllowableOffset": 0.00002,
        "resultOffset": p * PAGE, "resultRecordCount": PAGE,
    })
    for ft in d.get("features", []):
        pr = ft.get("properties", {})
        g = ft.get("geometry")
        if not g:
            continue
        try:
            pt = shape(g).representative_point()
        except Exception:
            continue
        rows.append({
            "parcelid": clean(pr.get("PARCELID")),
            "owner": clean(pr.get("OWNERNAME")),
            "addr": clean(pr.get("ADRLABEL")),
            "city": clean(pr.get("ADRCITY")),
            "subdiv": clean(pr.get("SUBDIV")),
            "lot": clean(pr.get("LOT")),
            "block": clean(pr.get("BLOCK")),
            "legal": clean(pr.get("PARCELLGL")),
            "total_value": pr.get("TOTALVALUE") or 0,
            "imp_value": pr.get("IMPVALUE") or 0,
            "assess_value": pr.get("ASSESSVAL") or 0,
            "parcel_type": clean(pr.get("PARCELTYPE")),
            "owner_city": clean(pr.get("OWNER_CITY")),
            "owner_st": clean(pr.get("OWNER_ST")),
            "lon": round(pt.x, 5),
            "lat": round(pt.y, 5),
        })
    if p % 15 == 0:
        print(f"  page {p + 1}/{npages}  rows={len(rows)}  {time.time() - t0:.0f}s", flush=True)

df = pd.DataFrame(rows).drop_duplicates("parcelid", keep="first")
print(f"unique parcels: {len(df)}  owners named: {(df.owner != '').mean() * 100:.1f}%  "
      f"addressed: {(df.addr != '').mean() * 100:.1f}%")
OUT_PKL.parent.mkdir(parents=True, exist_ok=True)
df.to_pickle(OUT_PKL)
print("wrote", OUT_PKL)

# ---------------------------------------------------------------- web index
idx = df[(df.owner != "") | (df.addr != "")].copy()
cities = sorted(c for c in idx.city.unique() if c)
city_i = {c: i for i, c in enumerate(cities)}
city_i[""] = -1

owners = []
for name, grp in idx.groupby("owner", sort=True):
    props = [[r.addr, city_i[r.city], r.lon, r.lat, int(r.total_value or 0)]
             for r in grp.itertuples()]
    owners.append([name, props])

out = {
    "generated": date.today().isoformat(),
    "count": len(idx),
    "cities": cities,
    "owners": owners,
}
OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
OUT_JSON.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
print(f"wrote {OUT_JSON} ({OUT_JSON.stat().st_size / 1e6:.1f} MB, "
      f"{len(owners)} owners, {len(idx)} properties)")

# validation: how many buildings will resolve an owner in the popup?
bf = ROOT / "data" / "processed" / "buildings_final.pkl"
if bf.exists():
    import re as _re

    def norm(x):
        return _re.sub(r"\s+", " ", _re.sub(r"[^A-Z0-9 ]+", " ", str(x).upper())).strip()

    b = pd.read_pickle(bf)
    keys = set((idx.addr.map(norm) + "|" + idx.city.map(norm)))
    bk = b.addr.map(norm) + "|" + b.city.map(norm)
    has = b.addr.astype(str).str.len() > 0
    print(f"buildings with an address: {has.mean() * 100:.1f}%; "
          f"of those, {bk[has].isin(keys).mean() * 100:.1f}% match an owner-index key")
print("DONE", flush=True)
