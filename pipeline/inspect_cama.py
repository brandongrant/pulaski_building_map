"""Print value distributions from CAMA tables to inform extraction mappings."""
import zipfile

import pandas as pd

from common.settings import RAW_DIR

ZIP = RAW_DIR / "CamaExport.zip"
z = zipfile.ZipFile(ZIP)


def load(part, usecols):
    fn = [f for f in z.namelist() if part in f][0]
    with z.open(fn) as fh:
        df = pd.read_csv(fh, sep="|", skiprows=[1], usecols=lambda c: c.strip() in usecols,
                         dtype=str, engine="c", na_filter=False)
    df.columns = [c.strip() for c in df.columns]
    for c in df.columns:
        df[c] = df[c].str.strip()
    return df


res = load("Residential_Buildings", {"ParcelNumber", "ImpNumber", "OccupancyType", "StoryHeight",
                                     "FirstFloorArea", "SecondFloorArea", "YearBuilt"})
print("=== Residential_Buildings:", len(res), "rows,", res.ParcelNumber.nunique(), "parcels")
print("--- StoryHeight:"); print(res.StoryHeight.value_counts().head(15).to_string())
print("--- OccupancyType:"); print(res.OccupancyType.value_counts().head(15).to_string())
yr = pd.to_numeric(res.YearBuilt, errors="coerce")
print("--- YearBuilt: null%%=%.1f zero%%=%.1f" % (yr.isna().mean() * 100, (yr == 0).mean() * 100))
print(yr[yr > 0].describe().to_string())
print("--- ImpNumber:"); print(res.ImpNumber.value_counts().head(5).to_string())

imp = load("Improvements", {"ParcelNumber", "ImpNumber", "Description", "Type"})
print("\n=== Improvements:", len(imp), "rows,", imp.ParcelNumber.nunique(), "parcels")
print("--- Description top 40:"); print(imp.Description.value_counts().head(40).to_string())
print("--- Type:"); print(imp.Type.value_counts().head(10).to_string())

com = load("Commercial_Sections", {"ParcelNumber", "ImpNumber", "Stories", "YearBuilt", "Area"})
print("\n=== Commercial_Sections:", len(com), "rows,", com.ParcelNumber.nunique(), "parcels")
cyr = pd.to_numeric(com.YearBuilt, errors="coerce")
print("--- YearBuilt: null%%=%.1f zero%%=%.1f" % (cyr.isna().mean() * 100, (cyr == 0).mean() * 100))
print(cyr[cyr > 0].describe().to_string())
print("--- Stories:"); print(com.Stories.value_counts().head(12).to_string())

mob = load("MobileHomeData", {"ParcelNumber", "YearBuilt", "SquareFootage"})
print("\n=== MobileHomeData:", len(mob), "rows")

use = load("UseCodesForParcels", {"ParcelNumber", "UseCode"})
print("\n=== UseCodesForParcels:", len(use), "rows,", use.ParcelNumber.nunique(), "parcels")
print(use.UseCode.value_counts().head(25).to_string())

print("\n--- sample ParcelNumbers:", res.ParcelNumber.head(3).tolist())
