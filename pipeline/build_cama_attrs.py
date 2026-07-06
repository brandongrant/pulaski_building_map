"""Build one attribute row per parcel from the Pulaski County CAMA export.

Output: data/processed/cama_parcel_attrs.parquet
  ParcelNumber, year_built, stories, sqft, category, imp_desc, n_bldgs
"""
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(r"D:\Claude Code Projects\Building_Map")
ZIP = ROOT / "data" / "raw" / "CamaExport.zip"
OUT = ROOT / "data" / "processed"
OUT.mkdir(parents=True, exist_ok=True)

z = zipfile.ZipFile(ZIP)


def load(part, usecols):
    fn = [f for f in z.namelist() if part in f][0]
    with z.open(fn) as fh:
        df = pd.read_csv(fh, sep="|", skiprows=[1], usecols=lambda c: c.strip() in usecols,
                         dtype=str, engine="c", na_filter=False, encoding="latin-1")
    df.columns = [c.strip() for c in df.columns]
    for c in df.columns:
        df[c] = df[c].str.strip()
    return df


def num(s):
    return pd.to_numeric(s, errors="coerce")


# ---------------- residential buildings ----------------
res = load("Residential_Buildings", {"ParcelNumber", "ImpNumber", "StoryHeight",
                                     "FirstFloorArea", "SecondFloorArea", "YearBuilt"})
res["yr"] = num(res.YearBuilt)
res.loc[(res.yr < 1650) | (res.yr > 2026), "yr"] = np.nan
res["sqft"] = num(res.FirstFloorArea).fillna(0) + num(res.SecondFloorArea).fillna(0)

# StoryHeight is a code; sanity-check against SecondFloorArea presence
chk = res.assign(has2nd=num(res.SecondFloorArea).fillna(0) > 0).groupby("StoryHeight").agg(
    n=("has2nd", "size"), pct_2nd=("has2nd", "mean"))
print("StoryHeight code vs has-second-floor:\n", chk.head(8).to_string())
STORY_MAP = {"0": 1.0, "1": 1.5, "2": 1.75, "3": 2.0, "4": 2.5}
res["stories"] = res.StoryHeight.map(STORY_MAP)

res_p = res.groupby("ParcelNumber").agg(
    res_yr=("yr", "min"), res_stories=("stories", "max"),
    res_sqft=("sqft", "sum"), res_n=("yr", "size")).reset_index()

# ---------------- commercial sections ----------------
com = load("Commercial_Sections", {"ParcelNumber", "Stories", "YearBuilt", "Area"})
com["yr"] = num(com.YearBuilt)
com.loc[(com.yr < 1650) | (com.yr > 2026), "yr"] = np.nan
com_p = com.groupby("ParcelNumber").agg(
    com_yr=("yr", "min"), com_stories=(com.columns[1] if "Stories" in com else "Stories", "max"),
    com_sqft=("Area", "sum"), com_n=("yr", "size")).reset_index()
com_p["com_stories"] = num(com_p.com_stories)
com_p.loc[(com_p.com_stories < 1) | (com_p.com_stories > 60), "com_stories"] = np.nan
com_p["com_sqft"] = num(com_p.com_sqft)
print("commercial max stories (clamped):", com_p.com_stories.max())

# ---------------- mobile homes ----------------
mob = load("MobileHomeData", {"ParcelNumber", "YearBuilt", "SquareFootage"})
mob["yr"] = num(mob.YearBuilt)
mob.loc[(mob.yr < 1900) | (mob.yr > 2026), "yr"] = np.nan
mob_p = mob.groupby("ParcelNumber").agg(mob_yr=("yr", "min"),
                                        mob_sqft=("SquareFootage", "sum")).reset_index()
mob_p["mob_sqft"] = num(mob_p.mob_sqft)

# ---------------- improvements (category source) ----------------
imp = load("Improvements", {"ParcelNumber", "ImpNumber", "Description", "Type"})
print("\nType x sample descriptions:")
print(imp.groupby("Type").Description.agg(lambda s: s.value_counts().head(4).index.tolist()).to_string())

imp["ImpNumber"] = num(imp.ImpNumber).fillna(99)
imp["desc"] = imp.Description.str.upper()


def rank_desc(d, t):
    """Lower rank = better candidate for the parcel's primary improvement."""
    if d.startswith("OBYI") or d == "" or d.startswith("VACANT"):
        return 5
    return 0


imp["rank"] = [rank_desc(d, t) for d, t in zip(imp.desc, imp.Type)]
imp = imp.sort_values(["ParcelNumber", "rank", "ImpNumber"])
prim = imp.drop_duplicates("ParcelNumber", keep="first")[["ParcelNumber", "desc", "Type"]]
prim = prim.rename(columns={"desc": "imp_desc", "Type": "imp_type"})

# ---------------- use codes (exempt flag) ----------------
use = load("UseCodesForParcels", {"ParcelNumber", "UseCode"})
print("\nUseCode top 25:\n", use.UseCode.value_counts().head(25).to_string())
exempt = set(use.loc[use.UseCode.str.upper() == "EXEMPT", "ParcelNumber"])
print("exempt parcels:", len(exempt))

# ---------------- merge ----------------
m = res_p.merge(com_p, on="ParcelNumber", how="outer") \
         .merge(mob_p, on="ParcelNumber", how="outer") \
         .merge(prim, on="ParcelNumber", how="outer")

m["year_built"] = m[["res_yr", "com_yr", "mob_yr"]].min(axis=1)
m["stories"] = m[["res_stories", "com_stories"]].max(axis=1)
m["sqft"] = m[["res_sqft", "com_sqft", "mob_sqft"]].sum(axis=1, min_count=1)
m.loc[(m.sqft < 0) | (m.sqft > 5_000_000), "sqft"] = np.nan  # CAMA garbage areas up to 1e205
m["n_bldgs"] = m[["res_n", "com_n"]].sum(axis=1, min_count=1).fillna(0).astype(int)
m["is_exempt"] = m.ParcelNumber.isin(exempt)
m["has_com"] = m.com_n.notna()


def categorize(r):
    d = r.imp_desc if isinstance(r.imp_desc, str) else ""
    if d.startswith("SINGLE FAMILY") or d == "TINYHOUSE":
        cat = "sfr"
    elif d == "TOWNHOUSE":
        cat = "sfr"
    elif d == "HPR" or d.startswith("HPR"):
        cat = "condo"
    elif d.startswith(("DUPLEX", "TRI-PLEX", "QUAD-PLEX")):
        cat = "plex"
    elif d.startswith("MOBILE HOME"):
        cat = "mobile"
    elif r.has_com or d.startswith("IMP") or r.imp_type == "1":
        cat = "com"
    elif d.startswith("OBYI"):
        cat = "obyi"
    else:
        cat = "unknown"
    if r.is_exempt and cat in ("com", "unknown"):
        cat = "exempt"
    return cat


m["category"] = m.apply(categorize, axis=1)
out = m[["ParcelNumber", "year_built", "stories", "sqft", "category", "imp_desc", "n_bldgs"]]
print("\ncategory counts:\n", out.category.value_counts().to_string())
print("\nyear_built coverage: %.1f%% of %d parcels" % (out.year_built.notna().mean() * 100, len(out)))
print("stories coverage: %.1f%%" % (out.stories.notna().mean() * 100))
out.to_pickle(OUT / "cama_parcel_attrs.pkl")
print("wrote", OUT / "cama_parcel_attrs.pkl", len(out), "rows")
