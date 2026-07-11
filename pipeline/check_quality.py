"""Data-quality release gate (roadmap QA-002).

Run at the end of the pipeline (run_all.py does). Compares the fresh build —
build_manifest.json quality metrics plus the regenerated web/data/config.json —
against floors and against the previously published config (from git HEAD).
A non-zero exit means: do NOT commit/push web/data; the build is suspect.

Publication in this project is "git push the regenerated web/data", so a
failing gate stops the bad data from replacing the prior good build even
though the local files were already rewritten — git still has the good ones.

Checks (thresholds are deliberately loose; tighten as history accumulates):
  building count      sane absolute range AND within tolerance of the
                      previously published count
  match rates         parcel->CAMA, building->parcel, year coverage floors
  bounds              config bounds stay inside the Pulaski County envelope
  freshness           CAMA effective date present, parseable, not in the future
"""
import json
import subprocess
import sys
from datetime import date, datetime

from common.provenance import BUILD_MANIFEST
from common.settings import REPO_ROOT, WEB_DATA_DIR

# floors/tolerances — current actuals: cama match ~97%, year rate ~90%,
# building->parcel (pid) coverage lands with the first post-ID-001 rebuild
THRESHOLDS = {
    "building_count_min": 150_000,
    "building_count_max": 400_000,
    "building_count_change_tolerance": 0.10,   # vs previously published
    # ~16% of parcels are vacant land with no CAMA improvement rows —
    # measured 0.842 on the 2026-07 export; floor set below the norm
    "parcel_to_cama_match_rate_min": 0.80,
    "building_to_parcel_match_rate_min": 0.90,
    "building_year_rate_min": 0.80,
}
# generous envelope around Pulaski County
COUNTY = {"lon_min": -93.5, "lon_max": -91.4, "lat_min": 33.9, "lat_max": 35.5}


def previous_config():
    """Last published config.json from git — the prior good build."""
    try:
        out = subprocess.check_output(
            ["git", "show", "HEAD:web/data/config.json"],
            cwd=REPO_ROOT, text=True, stderr=subprocess.DEVNULL)
        return json.loads(out)
    except Exception:
        return {}


def run_checks(quality, cfg, prev_cfg, thresholds=THRESHOLDS):
    """-> list of failure strings (empty = gate passes)."""
    fails = []
    t = thresholds

    n = cfg.get("count", 0)
    if not (t["building_count_min"] <= n <= t["building_count_max"]):
        fails.append(f"building count {n} outside sane range "
                     f"[{t['building_count_min']}, {t['building_count_max']}]")
    prev_n = prev_cfg.get("count")
    if prev_n:
        change = abs(n - prev_n) / prev_n
        if change > t["building_count_change_tolerance"]:
            fails.append(f"building count changed {change * 100:.1f}% vs published "
                         f"({prev_n} -> {n}); above "
                         f"{t['building_count_change_tolerance'] * 100:.0f}% tolerance")

    for key in ("parcel_to_cama_match_rate", "building_to_parcel_match_rate",
                "building_year_rate"):
        v = quality.get(key)
        if v is None:
            fails.append(f"quality metric missing: {key}")
        elif v < t[f"{key}_min"]:
            fails.append(f"{key} {v:.3f} below floor {t[f'{key}_min']}")

    b = cfg.get("bounds")
    if not b or len(b) != 4:
        fails.append("config bounds missing")
    elif not (COUNTY["lon_min"] <= b[0] and b[2] <= COUNTY["lon_max"]
              and COUNTY["lat_min"] <= b[1] and b[3] <= COUNTY["lat_max"]):
        fails.append(f"config bounds {b} escape the Pulaski County envelope")

    cd = cfg.get("cama_date", "")
    try:
        eff = datetime.strptime(cd, "%Y-%m-%d").date()
        if eff > date.today():
            fails.append(f"cama_date {cd} is in the future")
    except ValueError:
        fails.append(f"cama_date missing or unparseable: {cd!r}")

    return fails


def main():
    try:
        manifest = json.loads(BUILD_MANIFEST.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        print("QUALITY GATE: no build manifest — did the pipeline run?", file=sys.stderr)
        return 1
    try:
        cfg = json.loads((WEB_DATA_DIR / "config.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        print("QUALITY GATE: web/data/config.json missing/unreadable", file=sys.stderr)
        return 1

    quality = manifest.get("quality", {})
    fails = run_checks(quality, cfg, previous_config())

    print("quality metrics:", json.dumps(quality, indent=1, sort_keys=True))
    if fails:
        print("\nQUALITY GATE FAILED — do not commit/push web/data:", file=sys.stderr)
        for f in fails:
            print("  ✗", f, file=sys.stderr)
        return 1
    print("QUALITY GATE PASSED — safe to publish web/data")
    return 0


if __name__ == "__main__":
    sys.exit(main())
