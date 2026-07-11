"""Cut building polygons into MVT vector tiles and package as PMTiles.

Input:  data/processed/buildings_final.pkl (GeoDataFrame EPSG:4326)
Output: web/data/buildings.pmtiles + web/data/config.json
"""
import gzip
import json
import math
import time

import geopandas as gpd
import mapbox_vector_tile
import numpy as np
import pandas as pd
import shapely
from shapely.validation import make_valid

from pmtiles.tile import Compression, TileType, zxy_to_tileid
from pmtiles.writer import Writer

from common.settings import PROCESSED_DIR, WEB_DATA_DIR

OUT = WEB_DATA_DIR
OUT.mkdir(parents=True, exist_ok=True)

MINZ, MAXZ, EXTENT = 8, 15, 4096  # z8 so a phone can fit the whole county on screen
FULL_PROPS_Z = 13          # zoom at which addr/city strings appear
TINY_UNITS = 2.5           # min speck size in tile units at low zooms
ORIGIN = 20037508.342789244

print("loading...", flush=True)
gdf = pd.read_pickle(PROCESSED_DIR / "buildings_final.pkl")
gdf = gdf.set_geometry(shapely.force_2d(gdf.geometry.values), crs=gdf.crs)
merc = gdf.to_crs(3857)
gm = np.asarray(merc.geometry.values)
orig_bounds = shapely.bounds(gm)
n = len(gdf)
print(f"{n} buildings", flush=True)

# ---------------- properties ----------------
def col(name, default):
    if name in gdf.columns:
        return gdf[name].tolist()
    return [default] * n


def build_props(full):
    out = []
    it = zip(gdf.yr.tolist(), gdf.cat.tolist(), gdf.st.tolist(), gdf.main.tolist(),
             gdf.sqft.tolist(), gdf.fpa.tolist(), gdf.val.tolist(),
             gdf.addr.tolist(), gdf.city.tolist(),
             col("nveh", 0), col("ppv", 0), col("veh", ""))
    for yr, cat, st, main, sqft, fpa, val, addr, city, nveh, ppv, veh in it:
        p = {"yr": int(yr), "cat": int(cat), "main": int(main)}
        if st == st:  # not NaN
            p["st"] = round(float(st), 1)
        # numeric attrs at ALL zooms — they drive color ramps, so dropping
        # them below z13 made everything render as unknown-gray when
        # coloring by size/value zoomed out. At low zooms round them so the
        # MVT value table dedupes (keeps county-wide tiles small).
        if nveh:
            p["nveh"] = int(nveh)
        if full:
            if sqft:
                p["sqft"] = int(sqft)
            if fpa:
                p["fpa"] = int(fpa)
            if val:
                p["val"] = int(val)
            if ppv:
                p["ppv"] = int(ppv)
            if addr:
                p["addr"] = addr
            if city:
                p["city"] = city
            if veh:
                p["veh"] = veh
        else:
            if sqft:
                p["sqft"] = int(round(sqft, -1))
            if fpa:
                p["fpa"] = int(round(fpa, -1))
            if val:
                p["val"] = int(round(val, -3))
            if ppv:
                p["ppv"] = int(round(ppv, -3))
        out.append(p)
    return out


print("building property dicts...", flush=True)
props_lite = build_props(False)
props_full = build_props(True)
ids = gdf.id.astype(int).tolist()


def only_polys(g):
    """Reduce any geometry to its polygonal parts (or None)."""
    if g is None or g.is_empty:
        return None
    t = g.geom_type
    if t in ("Polygon", "MultiPolygon"):
        return g
    if t == "GeometryCollection":
        parts = [p for p in g.geoms if p.geom_type in ("Polygon", "MultiPolygon") and not p.is_empty]
        if not parts:
            return None
        return parts[0] if len(parts) == 1 else shapely.multipolygons(
            [q for p in parts for q in (p.geoms if p.geom_type == "MultiPolygon" else [p])])
    return None


def fix_invalid(shape):
    try:
        return only_polys(make_valid(shape))
    except Exception:
        return None


def tile_bounds(z, x, y):
    span = 2 * ORIGIN / (1 << z)
    minx = -ORIGIN + x * span
    maxy = ORIGIN - y * span
    return minx, maxy - span, minx + span, maxy


# data tile range
dminx, dminy, dmaxx, dmaxy = shapely.total_bounds(gm)
tiles = {}
t_all = time.time()

for z in range(MINZ, MAXZ + 1):
    t0 = time.time()
    span = 2 * ORIGIN / (1 << z)
    res = span / EXTENT
    tol = res * (0.8 if z <= 12 else 0.4)
    simp = shapely.simplify(gm, tol, preserve_topology=False)

    # replace empties and (at low zoom) sub-speck buildings with small squares
    b = orig_bounds
    cx, cy = (b[:, 0] + b[:, 2]) / 2, (b[:, 1] + b[:, 3]) / 2
    w, h = b[:, 2] - b[:, 0], b[:, 3] - b[:, 1]
    bad = shapely.is_empty(simp) | shapely.is_missing(simp)
    if z <= 12:
        s = TINY_UNITS * res
        tiny = (w < s) & (h < s)
        repl = tiny | bad
        half = np.maximum(np.maximum(w, h), s)[repl] / 2
        simp = simp.copy()
        simp[repl] = shapely.box(cx[repl] - half, cy[repl] - half, cx[repl] + half, cy[repl] + half)
    else:
        repl = bad
        if repl.any():
            simp = simp.copy()
            simp[repl] = gm[repl]

    tree = shapely.STRtree(simp)
    props = props_full if z >= FULL_PROPS_Z else props_lite

    x0 = max(0, int((dminx + ORIGIN) / span))
    x1 = min((1 << z) - 1, int((dmaxx + ORIGIN) / span))
    y0 = max(0, int((ORIGIN - dmaxy) / span))
    y1 = min((1 << z) - 1, int((ORIGIN - dminy) / span))
    nt, nf = 0, 0
    for ty in range(y0, y1 + 1):
        for tx in range(x0, x1 + 1):
            tminx, tminy, tmaxx, tmaxy = tile_bounds(z, tx, ty)
            buf = res * 32
            cand = tree.query(shapely.box(tminx - buf, tminy - buf, tmaxx + buf, tmaxy + buf))
            if len(cand) == 0:
                continue
            cand = np.sort(cand)
            clipped = shapely.clip_by_rect(simp[cand], tminx - buf, tminy - buf,
                                           tmaxx + buf, tmaxy + buf)
            feats = []
            for i, g in zip(cand, clipped):
                g = only_polys(g)
                if g is None:
                    continue
                feats.append({"geometry": g, "properties": props[i], "id": ids[i]})
            if not feats:
                continue
            data = mapbox_vector_tile.encode(
                [{"name": "buildings", "features": feats}],
                default_options={
                    "extents": EXTENT,
                    "quantize_bounds": (tminx, tminy, tmaxx, tmaxy),
                    "on_invalid_geometry": fix_invalid,
                    "y_coord_down": False,
                })
            tiles[zxy_to_tileid(z, tx, ty)] = gzip.compress(data, 6)
            nt += 1
            nf += len(feats)
    print(f"z{z}: {nt} tiles, {nf} feats, {time.time() - t0:.0f}s", flush=True)

# ---------------- write pmtiles ----------------
wgs = gdf.to_crs(4326)
lon0, lat0, lon1, lat1 = wgs.total_bounds
clon, clat = (lon0 + lon1) / 2, (lat0 + lat1) / 2
pm = OUT / "buildings.pmtiles"
with open(pm, "wb") as f:
    w = Writer(f)
    for tid in sorted(tiles):
        w.write_tile(tid, tiles[tid])
    w.finalize(
        {
            "tile_type": TileType.MVT,
            "tile_compression": Compression.GZIP,
            "min_lon_e7": int(lon0 * 1e7), "min_lat_e7": int(lat0 * 1e7),
            "max_lon_e7": int(lon1 * 1e7), "max_lat_e7": int(lat1 * 1e7),
            "min_zoom": MINZ, "max_zoom": MAXZ,
            "center_zoom": 11, "center_lon_e7": int(clon * 1e7), "center_lat_e7": int(clat * 1e7),
        },
        {
            "name": "Pulaski County Buildings",
            "format": "pbf",
            "vector_layers": [{
                "id": "buildings", "minzoom": MINZ, "maxzoom": MAXZ,
                "fields": {"yr": "Number", "cat": "Number", "st": "Number", "main": "Number",
                           "sqft": "Number", "fpa": "Number", "val": "Number",
                           "addr": "String", "city": "String"},
            }],
        })
print(f"wrote {pm} ({pm.stat().st_size / 1e6:.1f} MB, {len(tiles)} tiles) "
      f"in {(time.time() - t_all) / 60:.1f} min", flush=True)

# ---------------- config for the web app ----------------
yr = gdf.yr[gdf.yr > 0]
dec = (yr // 10 * 10).clip(lower=1850)
hist = dec.value_counts().sort_index()
cats = gdf.cat.value_counts().to_dict()
cfg = {
    "generated": time.strftime("%Y-%m-%d"),
    "cama_date": "2026-06-28",
    "count": int(n),
    "bounds": [round(float(v), 5) for v in (lon0, lat0, lon1, lat1)],
    "center": [round(float(clon), 5), round(float(clat), 5)],
    "minzoom": MINZ, "maxzoom": MAXZ,
    "year": {"min": int(yr.min()), "max": int(yr.max()),
             "p2": int(yr.quantile(0.02)), "p98": int(yr.quantile(0.98)),
             "known": int(len(yr))},
    "decades": {str(int(k)): int(v) for k, v in hist.items()},
    "cats": {str(int(k)): int(v) for k, v in cats.items()},
    "stories": {"max": float(np.nanmax(gdf.st.values)),
                "known": int(gdf.st.notna().sum())},
    "sqft": {"p5": int(gdf.sqft[gdf.sqft > 0].quantile(0.05)),
             "p99": int(gdf.sqft[gdf.sqft > 0].quantile(0.99))},
    "fpa": {"p5": int(gdf.fpa[gdf.fpa > 0].quantile(0.05)),
            "p99": int(gdf.fpa[gdf.fpa > 0].quantile(0.99))},
    "val": {"p5": int(gdf.val[gdf.val > 0].quantile(0.05)),
            "p99": int(gdf.val[gdf.val > 0].quantile(0.99))},
}
vm = (gdf.val > 0) & (gdf.sqft > 0)
vpsf = gdf.val[vm] / gdf.sqft[vm]
cfg["vpsf"] = {"p5": round(float(vpsf.quantile(0.05)), 1),
               "p50": round(float(vpsf.quantile(0.50)), 1),
               "p99": round(float(vpsf.quantile(0.99)), 1), "known": int(vm.sum())}
if "nveh" in gdf.columns:
    nv = gdf.nveh[gdf.nveh > 0]
    pv = gdf.ppv[gdf.ppv > 0]
    cfg["nveh"] = {"known": int(len(nv)), "p99": int(nv.quantile(0.99)), "max": int(nv.max())}
    cfg["ppv"] = {"p5": int(pv.quantile(0.05)), "p99": int(pv.quantile(0.99))}
(OUT / "config.json").write_text(json.dumps(cfg, indent=1), encoding="utf-8")
print("wrote config.json:", json.dumps(cfg)[:300], flush=True)
