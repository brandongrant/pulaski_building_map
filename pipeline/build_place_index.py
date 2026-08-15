"""Build a name index of PAgis's named buildings — the "what is at this address".

PAgis's building layer carries a ``BO_NAME`` on about 11.7k of its 225k
footprints, and those are precisely the ones people navigate by: stores,
hospitals, schools, churches, hotels, restaurants. Everything else is housing.

That gives the hotspot analysis a way to say "Walmart on Baseline" instead of
"8801 Baseline Rd", by matching an incident cluster's centre to the nearest
named footprint.

Output: web/data/places.json
    { generated, count, places: [[name, lon, lat], ...] }

Usage: python pipeline/build_place_index.py
"""
import json
import re
import sys
import time

import requests

from common.settings import WEB_DATA_DIR

URL = ("https://www.pagis.org/arcgis/rest/services/MAPS/BaseMap/MapServer/21/query")
PAGE = 1000
# Same trick build_owner_index.py uses: ask for coarse geometry so the transfer
# stays small — a building footprint's bounding-box centre is all we need.
PARAMS = {
    "where": "BO_NAME IS NOT NULL AND BO_NAME <> ''",
    "outFields": "BO_NAME",
    "returnGeometry": "true",
    "geometryPrecision": "6",
    "maxAllowableOffset": "0.00002",
    "outSR": "4326",
    "f": "json",
}


# About a fifth of the names identify one footprint inside a larger site —
# "Fair Oaks Apts - Bldg 8", "Baptist Health - Office". Nearest-building
# matching would then pin a whole complex's incidents on whichever building
# happened to be closest to the cluster centre, which is an artefact of the
# match, not a finding. Keep the site name, drop the sub-building.
SUBSITE_RE = re.compile(
    r"\s*[-–]\s*(bldg|building|bld|hngr|hangar|office|ofc|garage|laundry|"
    r"club\s?house|pool|gate|unit|apt|suite|ste)\b.*$", re.I)


def site_name(raw):
    return " ".join(SUBSITE_RE.sub("", raw or "").split())


def centroid(geom):
    """Bounding-box centre of an esriGeometryPolygon."""
    xs, ys = [], []
    for ring in geom.get("rings") or []:
        for x, y in ring:
            xs.append(x)
            ys.append(y)
    if not xs:
        return None
    return (round((min(xs) + max(xs)) / 2, 6), round((min(ys) + max(ys)) / 2, 6))


def main():
    places, offset = [], 0
    while True:
        p = dict(PARAMS, resultOffset=offset, resultRecordCount=PAGE)
        for attempt in range(4):
            try:
                r = requests.get(URL, params=p, timeout=120)
                r.raise_for_status()
                d = r.json()
                break
            except Exception as e:
                print(f"  retry {attempt + 1}: {e}", file=sys.stderr)
                time.sleep(5 * (attempt + 1))
        else:
            raise RuntimeError(f"could not fetch offset {offset}")

        feats = d.get("features") or []
        for f in feats:
            name = site_name(f["attributes"].get("BO_NAME"))
            c = centroid(f.get("geometry") or {})
            if name and c:
                places.append([name, c[0], c[1]])
        print(f"  offset {offset}: {len(feats)} features (total {len(places)})")
        if len(feats) < PAGE and not d.get("exceededTransferLimit"):
            break
        offset += PAGE

    out = WEB_DATA_DIR / "places.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "generated": time.strftime("%Y-%m-%d"),
        "count": len(places),
        "places": places,
    }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"places.json: {len(places)} named buildings, "
          f"{out.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
