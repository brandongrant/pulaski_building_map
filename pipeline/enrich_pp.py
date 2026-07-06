"""Aggregate current personal-property vehicles per address; join onto buildings.

Reads  data/processed/pp_rows.pkl (from extract_pp.py)
Updates data/processed/buildings_final.pkl with: nveh, ppv, veh
"""
import re
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(r"D:\Claude Code Projects\Building_Map")
PROC = ROOT / "data" / "processed"

YEAR_MIN = 2025          # an account's fleet is "current" if last assessed >= this
OPEN_ONLY = True         # drop CLOSED accounts
VEH_LIST_MAX = 6         # vehicles named in the tooltip string

pp = pd.read_pickle(PROC / "pp_rows.pkl")
print(f"{len(pp)} rows loaded")
for c in pp.columns:
    pp[c] = pp[c].astype(str).str.strip().replace("None", "")

pp["ay"] = pd.to_numeric(pp["Assess Year"], errors="coerce").fillna(0).astype(int)
pp["av"] = pd.to_numeric(pp["Assessed Value"], errors="coerce").fillna(0)
pp.loc[(pp.av < 0) | (pp.av > 5e7), "av"] = 0
pp["pyr"] = pd.to_numeric(pp["Prop Year"], errors="coerce").fillna(0).astype(int)

print("PPAN Status:", pp["PPAN Status"].value_counts().head(6).to_dict())
print("PPAN Type:", pp["PPAN Type"].value_counts().head(6).to_dict())
print("Vehicle Type:", pp["Vehicle Type"].value_counts().head(8).to_dict())
print("Assess Year:", pp.ay.value_counts().sort_index().tail(6).to_dict())

# ---- current snapshot: latest assess-year per account, recent, open ----
if OPEN_ONLY:
    pp = pp[pp["PPAN Status"].str.upper() != "CLOSED"]
last = pp.groupby("PPAN").ay.transform("max")
pp = pp[(pp.ay == last) & (pp.ay >= YEAR_MIN)]
print(f"current rows: {len(pp)} across {pp.PPAN.nunique()} accounts")

# ---- what counts as a vehicle ----
# Vehicle Type blank/'0'/'99' rows are business equipment (MACHINERY EQUIPMENT,
# FURNITURE FIXTURES, ...); codes 1-98 with a real make/model are vehicles
# (cars, trucks, boats, trailers, cycles).
vt = pd.to_numeric(pp["Vehicle Type"], errors="coerce").fillna(-1)
EQUIP_RE = (r"EQUIPMENT|FURNITURE|FIXTURE|COMPUTER|COMMUNICATION|SIGN|SUPP|"
            r"INVENTORY|LEASEHOLD|SECURITY|MACHINERY|TOOLS")
is_veh = ((vt >= 1) & (vt <= 98) & (pp.Make != "") & (pp.Model != "")
          & ~pp.Make.str.contains(EQUIP_RE, regex=True))
print(f"vehicle rows: {int(is_veh.sum())} "
      f"(P: {int((is_veh & (pp['PPAN Type'] == 'P')).sum())}, "
      f"B: {int((is_veh & (pp['PPAN Type'] == 'B')).sum())})")

# ---- address key ----
UNIT_RE = re.compile(r"\b(APT|UNIT|STE|SUITE|LOT|BLDG|RM|TRLR|#)\b.*$")
NONALNUM = re.compile(r"[^A-Z0-9 ]+")
WS = re.compile(r"\s+")


def norm_addr(a1, a2):
    a = a1 if (a1 and a1[:1].isdigit()) else (a2 if a2 and a2[:1].isdigit() else a1 or a2)
    a = NONALNUM.sub(" ", a.upper())
    a = UNIT_RE.sub("", a)
    return WS.sub(" ", a).strip()


CITY_MAP = {
    "N LITTLE ROCK": "NORTH LITTLE ROCK", "NLR": "NORTH LITTLE ROCK",
    "NO LITTLE ROCK": "NORTH LITTLE ROCK", "N LITTLE RO": "NORTH LITTLE ROCK",
    "LR": "LITTLE ROCK", "JAX": "JACKSONVILLE", "LITTLEROCK": "LITTLE ROCK",
}


def norm_city(c):
    c = WS.sub(" ", NONALNUM.sub(" ", c.upper())).strip()
    return CITY_MAP.get(c, c)


pp["ak"] = [norm_addr(a, b) for a, b in zip(pp.Address1, pp.Address2)]
pp["ck"] = pp.City.map(norm_city)
pp = pp[pp.ak != ""]

pp["vdesc"] = ""
pyr = pp.pyr.where(pp.pyr > 1900, 0)
pp.loc[is_veh, "vdesc"] = (pyr.astype(str).str.replace("^0$", "", regex=True) + " "
                           + pp.Make + " " + pp.Model).str.strip().str.slice(0, 34)
pp["is_veh"] = is_veh.astype(int)

def veh_list(s):
    vs = [v for v in s if v]
    out = "; ".join(vs[:VEH_LIST_MAX])
    if len(vs) > VEH_LIST_MAX:
        out += f"; +{len(vs) - VEH_LIST_MAX} more"
    return out.replace("<", "").replace(">", "").replace("&", "and")[:180]


agg = pp.sort_values("av", ascending=False).groupby(["ak", "ck"]).agg(
    nveh=("is_veh", "sum"), ppv=("av", "sum"), veh=("vdesc", veh_list)).reset_index()
agg["ppv"] = agg.ppv.round().astype("int64")
agg["nveh"] = agg.nveh.astype("int16")
print(f"{len(agg)} distinct PP addresses; top cities: {agg.ck.value_counts().head(8).to_dict()}")

# ---- join to buildings ----
b = pd.read_pickle(PROC / "buildings_final.pkl")
b["ak"] = [norm_addr(a, "") for a in b.addr.fillna("")]
b["ck"] = b.city.fillna("").map(norm_city)
print("building cities:", b.ck.value_counts().head(8).to_dict())

j = b[["ak", "ck"]].merge(agg, on=["ak", "ck"], how="left")
matched_ac = j.nveh.notna()

# fallback: address-only where the address is unambiguous in the PP data
agg2 = agg[~agg.ak.duplicated(keep=False)].set_index("ak")
need = ~matched_ac & b.ak.isin(agg2.index)
for colname in ("nveh", "ppv", "veh"):
    j.loc[need, colname] = b.ak[need].map(agg2[colname]).values

print(f"addr+city matches: {int(matched_ac.sum())}, +addr-only fallback: {int(need.sum())}")
b["nveh"] = pd.to_numeric(j.nveh, errors="coerce").fillna(0).astype("int16")
b["ppv"] = pd.to_numeric(j.ppv, errors="coerce").fillna(0).astype("int64")
b["veh"] = j.veh.fillna("")

bldg_addrs = set(b.ak)
pct_pp = agg.ak.isin(bldg_addrs).mean() * 100
b = b.drop(columns=["ak", "ck"])
print(f"\nbuildings with vehicles: {int((b.nveh > 0).sum())} ({(b.nveh > 0).mean() * 100:.1f}%)")
print(f"PP addresses matched to a building: {pct_pp:.1f}%")
print("nveh distribution:", b.nveh[b.nveh > 0].describe()[["mean", "50%", "max"]].round(2).to_dict())
print("ppv p50/p99:", int(b.ppv[b.ppv > 0].median()), int(b.ppv[b.ppv > 0].quantile(0.99)))
b.to_pickle(PROC / "buildings_final.pkl")
print("updated buildings_final.pkl")
