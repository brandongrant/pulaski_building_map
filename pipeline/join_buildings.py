"""Join CAMA parcel attributes -> parcel polygons -> building footprints.

Output: data/processed/buildings_final.pkl  (GeoDataFrame, EPSG:4326)
"""
import re
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW, OUT = ROOT / "data" / "raw", ROOT / "data" / "processed"

CAT_CODE = {"unknown": 0, "sfr": 1, "condo": 2, "plex": 3, "mobile": 4,
            "com": 5, "exempt": 6, "obyi": 7}


def norm_key(s):
    return s.str.upper().str.replace(r"[^0-9A-Z]", "", regex=True)


print("reading parcels...", flush=True)
par = gpd.read_file(RAW / "parcels.geojson")
print(f"  {len(par)} parcels; PARCELID sample: {par.PARCELID.dropna().head(3).tolist()}")

cama = pd.read_pickle(OUT / "cama_parcel_attrs.pkl")
print(f"  {len(cama)} cama rows; ParcelNumber sample: {cama.ParcelNumber.head(3).tolist()}")

par["k"] = norm_key(par.PARCELID.astype(str))
cama["k"] = norm_key(cama.ParcelNumber.astype(str))
cama = cama.drop_duplicates("k")
match = par.k.isin(set(cama.k)).mean()
print(f"  parcel->cama key match rate: {match * 100:.1f}%")

par = par.merge(cama, on="k", how="left")
par["year_built"] = pd.to_numeric(par.year_built, errors="coerce")

print("reading buildings...", flush=True)
bld = gpd.read_file(RAW / "buildings.geojson")
print(f"  {len(bld)} buildings; BO_CODE counts:\n{bld.BO_CODE.value_counts().head(8).to_string()}")

# representative point spatial join
pts = bld[["OBJECTID"]].copy()
pts = gpd.GeoDataFrame(pts, geometry=bld.geometry.representative_point(), crs=bld.crs)
keep = ["year_built", "stories", "sqft", "category", "ADRLABEL", "ADRCITY",
        "PARCELTYPE", "IMPVALUE", "TOTALVALUE", "geometry"]
j = gpd.sjoin(pts, par[keep], how="left", predicate="within")
# stacked/duplicate parcels (condos): prefer the match that has a year
j = j.sort_values(["OBJECTID", "year_built"], na_position="last")
j = j.drop_duplicates("OBJECTID", keep="first")
print(f"  joined: {j.year_built.notna().mean() * 100:.1f}% of buildings got a year")

bld = bld.merge(j.drop(columns=["geometry", "index_right"], errors="ignore"), on="OBJECTID", how="left")

# footprint area in sqft (via UTM 15N meters)
area_m2 = bld.geometry.to_crs(26915).area
bld["fpa"] = (area_m2 * 10.7639).round().astype("int64")

# main building per parcel = largest footprint (parcel key from join)
bld["_pk"] = j.set_index("OBJECTID").reindex(bld.OBJECTID)["index_right"].values
bld["main"] = 0
has_pk = bld._pk.notna()
idx_max = bld[has_pk].groupby("_pk")["fpa"].idxmax()
bld.loc[idx_max, "main"] = 1
bld.loc[~has_pk, "main"] = 1  # unmatched buildings count as main
print(f"  main buildings: {bld.main.sum()} of {len(bld)}")

def clean_num(s, hi):
    """CAMA has garbage magnitudes (areas up to 1e205); clamp outside [0, hi] to 0."""
    v = pd.to_numeric(s, errors="coerce").fillna(0)
    v[(v < 0) | (v > hi)] = 0
    return v.astype("int64")


out = gpd.GeoDataFrame({
    "id": bld.OBJECTID.astype("int64"),
    "yr": bld.year_built.fillna(0).astype("int32"),
    "cat": bld.category.map(CAT_CODE).fillna(0).astype("int16"),
    "st": pd.to_numeric(bld.stories, errors="coerce"),
    "sqft": clean_num(bld.sqft, 5_000_000),
    "fpa": bld.fpa,
    "val": clean_num(bld.IMPVALUE, 2_000_000_000),
    "addr": bld.ADRLABEL.fillna(""),
    "city": bld.ADRCITY.fillna(""),
    "main": bld.main.astype("int16"),
}, geometry=bld.geometry, crs="EPSG:4326")

# sanity
print("\nyear distribution (buildings):")
yb = out.yr[out.yr > 0]
print(yb.describe().to_string())
print("cat counts:", out.cat.value_counts().to_dict())
out.to_pickle(OUT / "buildings_final.pkl")
print("wrote", OUT / "buildings_final.pkl", len(out))
