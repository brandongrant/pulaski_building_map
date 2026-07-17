"""Build a compact reported-crime layer from an LRPD incident-statistics CSV.

Input: the Little Rock Police Department "Statistics 2017 to Year to Date"
export (index/Part-I offenses). It already carries LATITUDE/LONGITUDE, so no
geocoding is needed — rows without coordinates (LRPD suppresses some) are
counted but not plotted.

Columns used: INCIDENT_DATE, OFFENSE_DESCRIPTION, WEAPON_TYPE,
INCIDENT_LOCATION, LATITUDE, LONGITUDE, Offense Status.

Output (web/data/crime/):
  crimes.json       compact interned flat table; the browser merges these rows
                    into the dispatch overlay's all-time layer (small on the
                    wire — same shape idea as vehicles.json):
    { generated, count, not_plotted, bbox, year_min, year_max,
      offenses:[desc,...], off_cat:[cat_key,...],   # parallel to offenses
      statuses:[code,...], weapons:[desc,...], locs:[address,...],
      by_cat:{...}, by_year:{...},
      crime:[[lon,lat,offIdx,yyyymmdd,statusIdx,weaponIdx,locIdx], ...] }
  crimes_meta.json  tiny summary for the panel label (no big fetch)

Usage:
  python build_crime.py --csv <path-to-lrpd.csv>
"""
import argparse
import csv
import json
from collections import Counter
from datetime import date, datetime

from common.settings import RAW_DIR, WEB_DATA_DIR

# Offense description -> DISPATCH category key (DSP_CATS / CAT_RULES in
# dispatch_collect.py + app.js). The crime points fold INTO the dispatch overlay
# and are filtered by the same category chips, so these map onto the dispatch
# taxonomy: the 14 index offenses are all violent or property crime, which land
# in assault / robbery / sex / burglary / theft. The specific offense
# description is preserved for the popup.
OFFENSE_CAT = {
    "MURDER & NONNEGLIGENT MANSLAUGHTER": "assault",  # dispatch assault covers homicide/murder
    "AGGRAVATED ASSAULT": "assault",
    "ROBBERY": "robbery",
    "PURSE-SNATCHING": "robbery",
    "RAPE": "sex",
    "BURGLARY/B&E": "burglary",
    "MOTOR VEHICLE THEFT": "theft",
    "THEFT FROM MOTOR VEHICLE": "theft",
    "THEFT OF MOTOR VEHICLE PARTS": "theft",
    "SHOPLIFTING": "theft",
    "ALL OTHER LARCENY": "theft",
    "THEFT FROM BUILDING": "theft",
    "POCKET-PICKING": "theft",
    "THEFT FROM COIN-OPERATED MACHINE": "theft",
}


class Intern:
    def __init__(self):
        self.list = []
        self._i = {}

    def idx(self, v):
        i = self._i.get(v)
        if i is None:
            i = self._i[v] = len(self.list)
            self.list.append(v)
        return i


def parse_date(s):
    s = (s or "").strip()
    for fmt in ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=str(RAW_DIR / "lrpd_crime.csv"))
    args = ap.parse_args()

    offenses, statuses, weapons, locs = Intern(), Intern(), Intern(), Intern()
    rows = []
    total = not_plotted = 0
    by_cat, by_year = Counter(), Counter()
    minlon = minlat = 1e18
    maxlon = maxlat = -1e18
    ymin, ymax = 9999, 0
    unmapped = Counter()

    with open(args.csv, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            total += 1
            try:
                lon = round(float(row["LONGITUDE"]), 5)
                lat = round(float(row["LATITUDE"]), 5)
            except (TypeError, ValueError, KeyError):
                not_plotted += 1
                continue
            # sanity box: Pulaski County-ish; drop 0/0 and out-of-area
            if not (33.5 < lat < 35.5 and -93.5 < lon < -91.5):
                not_plotted += 1
                continue
            d = parse_date(row.get("INCIDENT_DATE"))
            if not d:
                not_plotted += 1
                continue
            off = (row.get("OFFENSE_DESCRIPTION") or "").strip().upper()
            cat = OFFENSE_CAT.get(off, "other")
            if cat == "other" and off:
                unmapped[off] += 1
            status = (row.get("Offense Status") or "").strip().upper()
            weapon = (row.get("WEAPON_TYPE") or "").strip().upper()
            loc = " ".join((row.get("INCIDENT_LOCATION") or "").upper().split())

            rows.append([lon, lat, offenses.idx(off),
                         d.year * 10000 + d.month * 100 + d.day,
                         statuses.idx(status), weapons.idx(weapon), locs.idx(loc)])
            by_cat[cat] += 1
            by_year[d.year] += 1
            minlon, maxlon = min(minlon, lon), max(maxlon, lon)
            minlat, maxlat = min(minlat, lat), max(maxlat, lat)
            ymin, ymax = min(ymin, d.year), max(ymax, d.year)

    off_cat = [OFFENSE_CAT.get(o, "other") for o in offenses.list]
    out_dir = WEB_DATA_DIR / "crime"
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated": date.today().isoformat(),
        "count": len(rows),
        "not_plotted": not_plotted,
        "bbox": [round(minlon, 5), round(minlat, 5), round(maxlon, 5), round(maxlat, 5)],
        "year_min": ymin, "year_max": ymax,
        "offenses": offenses.list, "off_cat": off_cat,
        "statuses": statuses.list, "weapons": weapons.list, "locs": locs.list,
        "by_cat": dict(by_cat), "by_year": {str(k): v for k, v in sorted(by_year.items())},
        "crime": rows,
    }
    (out_dir / "crimes.json").write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    (out_dir / "crimes_meta.json").write_text(json.dumps({
        "generated": payload["generated"], "count": len(rows),
        "not_plotted": not_plotted, "year_min": ymin, "year_max": ymax,
        "by_cat": dict(by_cat),
    }), encoding="utf-8")

    size = (out_dir / "crimes.json").stat().st_size / 1e6
    print(f"rows in CSV: {total}, plotted: {len(rows)}, not plotted: {not_plotted}")
    print(f"offenses: {len(offenses.list)}, statuses: {len(statuses.list)}, "
          f"weapons: {len(weapons.list)}, years {ymin}-{ymax}")
    print(f"by_cat: {dict(by_cat)}")
    if unmapped:
        print(f"UNMAPPED offense descriptions (fell to 'other'): {dict(unmapped)}")
    print(f"crimes.json: {size:.1f} MB")


if __name__ == "__main__":
    main()
