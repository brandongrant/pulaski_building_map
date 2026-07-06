"""Download an ArcGIS REST layer as GeoJSON with pagination + resume.

Usage:
  python download_layer.py <layer_url> <name> <out_dir> <outFields> [page_size]
"""
import json
import shutil
import sys
import time
from pathlib import Path

import requests


def main():
    url, name, out_dir, fields = sys.argv[1], sys.argv[2], Path(sys.argv[3]), sys.argv[4]
    page = int(sys.argv[5]) if len(sys.argv) > 5 else 1000
    parts = out_dir / f"{name}_parts"
    parts.mkdir(parents=True, exist_ok=True)
    s = requests.Session()

    def get(params, tries=6):
        last = None
        for i in range(tries):
            try:
                r = s.get(url + "/query", params=params, timeout=180)
                r.raise_for_status()
                d = r.json()
                if "error" in d:
                    raise RuntimeError(d["error"])
                return d
            except Exception as e:
                last = e
                print(f"  retry {i + 1}: {e}", flush=True)
                time.sleep(3 * (i + 1))
        raise RuntimeError(f"gave up on {params.get('resultOffset')}: {last}")

    count = get({"where": "1=1", "returnCountOnly": "true", "f": "json"})["count"]
    npages = (count + page - 1) // page
    print(f"{name}: {count} features, {npages} pages", flush=True)
    t0 = time.time()
    for p in range(npages):
        f = parts / f"p{p:05d}.geojson"
        if f.exists() and f.stat().st_size > 50:
            continue
        d = get({
            "where": "1=1", "outFields": fields, "returnGeometry": "true",
            "f": "geojson", "outSR": "4326",
            "resultOffset": p * page, "resultRecordCount": page,
        })
        feats = d.get("features", [])
        if p < npages - 1 and len(feats) != page:
            print(f"WARNING: page {p} returned {len(feats)} != {page}", flush=True)
        f.write_text(json.dumps(d), encoding="utf-8")
        if p % 20 == 0:
            print(f"  page {p + 1}/{npages} ({len(feats)} feats) {time.time() - t0:.0f}s", flush=True)

    out = out_dir / f"{name}.geojson"
    n = 0
    seen = set()
    with open(out, "w", encoding="utf-8") as w:
        w.write('{"type":"FeatureCollection","features":[\n')
        first = True
        for p in range(npages):
            d = json.loads((parts / f"p{p:05d}.geojson").read_text(encoding="utf-8"))
            for ft in d.get("features", []):
                fid = ft.get("id")
                if fid is not None:
                    if fid in seen:
                        continue
                    seen.add(fid)
                if not first:
                    w.write(",\n")
                w.write(json.dumps(ft))
                first = False
                n += 1
        w.write("\n]}\n")
    print(f"merged {n} unique features -> {out} ({out.stat().st_size / 1e6:.1f} MB)", flush=True)
    shutil.rmtree(parts)
    print("DONE", flush=True)


main()
