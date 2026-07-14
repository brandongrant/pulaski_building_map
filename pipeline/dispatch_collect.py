"""Collect Little Rock public dispatch events and publish map-ready outputs.

Designed to run on a GitHub Actions runner (stdlib + requests only).

Usage: python pipeline/dispatch_collect.py --store <data-branch-checkout-dir>

Store layout:
  dispatch/address_index.json.gz   canonical address + house-number street index
  dispatch/raw/YYYY-MM.jsonl       append-only archive of unique events (keeps
                                   the raw location string so re-geocoding on
                                   every rebuild retroactively fixes history)
  dispatch/out/recent_24h.geojson  points, last 24 h
  dispatch/out/recent_7d.geojson   bare points for the heatmap, last 7 d
  dispatch/out/grid_30d.geojson    ~500 ft grid cells with per-category counts
  dispatch/out/all.geojson         every geocoded point, all-time (indefinite)
  dispatch/out/stats.json          totals + collection metadata

Geocoding (fixed 2026-07-13): the location string is canonicalized (street-type
and direction synonyms folded — "CHENAL PKY" -> "CHENAL PKWY"), matched exactly,
then interpolated by house number along the street. A call is only placed as a
precise point when its address is verified that way (or it is a real
intersection); the old street-centroid fallback — which silently dropped every
un-matched call on a street onto one wrong averaged point, creating phantom
hotspots — is gone. Un-verifiable calls are counted but not pinned.

Display policy note: as of 2026-07-13 the site owner opted to map ALL call
types, including the medical/welfare/mental-health/death/sex/domestic/juvenile
types this collector still flags via ``sens`` (see docs + jurisdictions/ar/
pulaski.yml). The flag is retained as metadata; it no longer suppresses points.
"""
import argparse
import bisect
import gzip
import hashlib
import json
import math
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from addr_norm import norm, canon_addr

URL = "https://web.littlerock.state.ar.us/pub/Home/CadEvents"
JURISDICTION = "Little Rock"
LOCAL_TZ = ZoneInfo("America/Chicago")
GRID_FT = 500.0

# Ordered category rules (first match wins). Keys/labels/colors are mirrored in
# the web overlay (DSP_CATS in app.js); keep them in sync. Expanded 2026-07-13
# from 9 coarse buckets to the full call-type breakdown.
# Order = match priority. Weapon/violence and the specific person-categories
# (domestic, sex, juvenile, welfare) sit ahead of the generic "disturbance"
# bucket so "DOMESTIC DISTURBANCE" -> domestic and "MENTAL … DISTURBANCE" ->
# welfare; "animal" precedes welfare so "INJURED ANIMAL" isn't read as medical.
CAT_RULES = [
    ("shots",       r"SHOT ?SPOTTER|SHOTS? FIRED|SHOOTING|WEAPON|ARMED|\bGUN\b"),
    ("alarm",       r"ALARM"),
    ("assault",     r"BATTERY|ASSAULT|STABB|\bFIGHT|HOMICIDE|MURDER|CUTTING"),
    ("robbery",     r"ROBBERY"),
    ("burglary",    r"BURGLARY|BREAKING|PROWLER|\bB ?& ?E\b"),
    ("theft",       r"THEFT|STOLEN|SHOPLIFT|LARCENY|PURSE SNATCH|AUTO"),
    ("fraud",       r"FRAUD|FORGERY|COUNTERFEIT|SCAM|EMBEZZL|IDENTITY"),
    ("vandalism",   r"VANDAL|CRIMINAL MISCHIEF|GRAFFITI|DAMAGE"),
    ("drugs",       r"NARCOTIC|\bDRUG|METH\b|OVERDOSE"),  # overdose also sens-flagged
    ("animal",      r"ANIMAL|\bDOG\b|VICIOUS"),
    ("domestic",    r"DOMESTIC"),
    ("sex",         r"RAPE|SEXUAL|SEX OFFENSE|INDECENT|PEEPING"),
    ("juvenile",    r"JUVENILE|RUNAWAY|CURFEW"),
    ("welfare",     r"MEDICAL|CHECK CONDITION|SUBJECT DOWN|WELFARE|SUICID|MENTAL|SICK|INJURED|UNCONSCIOUS|CARDIAC|DEATH|DECEASED|MISSING"),
    ("traffic",     r"ACCIDENT|TRAFFIC|VEHICLE|PARKING|WRECKER|MOTORIST|HIT ?& ?RUN|DUI|DWI|RECKLESS|STALLED"),
    ("disturbance", r"DISTURBANCE|LOUD|NOISE|FIREWORK|PARTY|VERBAL|HARASS|THREAT"),
    ("trespass",    r"TRESPASS|LOITER|UNWANTED|SOLICIT"),
    ("suspicious",  r"SUSPICIOUS|UNKNOWN|PROWL"),
    ("assist",      r"ADMINISTRATIVE|INFORMATION|PRISONER|TRANSPORT|ESCORT|STANDBY|ASSIST|RELAY|PROPERTY CHECK|911|HANG ?UP|SERVICE|FLAG DOWN|FOLLOW|WARRANT|DIRECTED PATROL"),
]
CAT_KEYS = [k for k, _ in CAT_RULES] + ["other"]
CAT_RE = [(k, re.compile(p)) for k, p in CAT_RULES]

# Sensitive call types — retained as an informational flag (``sens``); the site
# owner opted to map them, so this no longer filters points (see module docstring).
SENSITIVE_RE = re.compile(
    r"MEDICAL|DEATH|SUBJECT DOWN|CONDITION|WELFARE|SUICID|OVERDOSE|MENTAL|JUVENILE|RAPE|SEX|DOMESTIC")


def categorize(t):
    for key, rx in CAT_RE:
        if rx.search(t):
            return key
    return "other"


class Geocoder:
    """Canonical exact match -> house-number interpolation -> intersection.

    No street-centroid fallback: a precise point is only returned for a
    verified address or a real intersection. ``geocode`` quality is one of
    exact_address / interpolated / intersection / failed.
    """

    def __init__(self, index_path):
        with gzip.open(index_path, "rt", encoding="utf-8") as f:
            d = json.load(f)
        self.addr = d["addr"]        # canonical "708 MAIN ST" -> [lon, lat]
        self.streets = d["streets"]  # canonical "MAIN ST" -> [[lon,lat,num], ...]
        # precompute sorted house-number arrays per street for bisect
        self._nums = {}
        for k, pts in self.streets.items():
            if pts and len(pts[0]) >= 3:
                self._nums[k] = [p[2] for p in pts]

    def _street_key(self, name):
        name = name.strip()
        if name in self.streets:
            return name
        parts = name.split(" ")
        if len(parts) > 1 and " ".join(parts[:-1]) in self.streets:
            return " ".join(parts[:-1])
        return None

    def _interpolate(self, street_key, num):
        pts = self.streets.get(street_key)
        nums = self._nums.get(street_key)
        if not pts or not nums:
            return None
        lo, hi = nums[0], nums[-1]
        span = max(1, hi - lo)
        tol = max(20, int(0.1 * span))            # tolerate a little past each end
        if num < lo:
            return (pts[0][0], pts[0][1]) if lo - num <= tol else None
        if num > hi:
            return (pts[-1][0], pts[-1][1]) if num - hi <= tol else None
        i = bisect.bisect_left(nums, num)
        if i < len(nums) and nums[i] == num:
            return pts[i][0], pts[i][1]
        n0, n1 = nums[i - 1], nums[i]
        p0, p1 = pts[i - 1], pts[i]
        t = (num - n0) / (n1 - n0) if n1 != n0 else 0
        return p0[0] + t * (p1[0] - p0[0]), p0[1] + t * (p1[1] - p0[1])

    def geocode(self, loc):
        """-> (lon, lat, quality) or (None, None, 'failed')."""
        q = canon_addr(loc)
        if "/" in q:                              # intersection
            a, _, b = (s.strip() for s in q.partition("/"))
            ka, kb = self._street_key(a), self._street_key(b)
            if ka and kb:
                pa, pb = self.streets[ka], self.streets[kb]
                best, bd = None, 1e18
                for x1, y1, *_ in pa[:400]:
                    for x2, y2, *_ in pb[:400]:
                        dd = (x1 - x2) ** 2 + (y1 - y2) ** 2
                        if dd < bd:
                            bd, best = dd, ((x1 + x2) / 2, (y1 + y2) / 2)
                if best and bd < (0.01 ** 2):     # streets actually meet (~1 km)
                    return best[0], best[1], "intersection"
            return None, None, "failed"
        if q in self.addr:
            lon, lat = self.addr[q]
            return lon, lat, "exact_address"
        m = re.match(r"^(\d+) (.+)$", q)
        if m:
            num, rest = int(m.group(1)), m.group(2)
            key = self._street_key(rest)
            if key:
                pt = self._interpolate(key, num)
                if pt:
                    return pt[0], pt[1], "interpolated"
        return None, None, "failed"


def fetch_events():
    for i in range(5):
        try:
            r = requests.post(URL, timeout=60, headers={"Content-Length": "0"})
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"fetch retry {i + 1}: {e}", file=sys.stderr)
            time.sleep(10 * (i + 1))
    raise RuntimeError("could not fetch dispatch feed")


# ---- web-mercator helpers for the grid -------------------------------------
_R = 6378137.0


def _merc(lon, lat):
    return (math.radians(lon) * _R,
            _R * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))


def _unmerc(x, y):
    return (math.degrees(x / _R),
            math.degrees(2 * math.atan(math.exp(y / _R)) - math.pi / 2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--rebuild-only", action="store_true",
                    help="no fetch; re-geocode the archive and rewrite outputs")
    args = ap.parse_args()
    store = Path(args.store) / "dispatch"
    raw_dir = store / "raw"
    out_dir = store / "out"
    raw_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    geo = Geocoder(store / "address_index.json.gz")
    now = datetime.now(timezone.utc)

    # ---------------- fetch + append new events ----------------
    if not args.rebuild_only:
        months = [(now - timedelta(days=d)).strftime("%Y-%m") for d in (0, 28)]
        seen = set()
        for m in dict.fromkeys(months):
            f = raw_dir / f"{m}.jsonl"
            if f.exists():
                for line in f.read_text(encoding="utf-8").splitlines():
                    try:
                        seen.add(json.loads(line)["id"])
                    except Exception:
                        pass
        events = fetch_events()
        new = []
        for e in events:
            t = str(e.get("typeDescription", "")).strip().upper()
            loc = str(e.get("location", "")).strip().upper()
            ts = str(e.get("dispatchDate", "")).strip()
            if not (t and loc and ts):
                continue
            eid = hashlib.sha256(f"LR|{t}|{loc}|{ts}".encode()).hexdigest()[:20]
            if eid in seen:
                continue
            seen.add(eid)
            try:
                dt = datetime.strptime(ts, "%m/%d/%Y %H:%M:%S").replace(tzinfo=LOCAL_TZ)
            except ValueError:
                continue
            new.append({
                "id": eid, "src": "CLR-CAD", "jur": JURISDICTION,
                "type": t, "cat": categorize(t), "loc": loc,
                "ts": dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "sens": 1 if SENSITIVE_RE.search(t) else 0,
                "seen": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            })
        if new:
            by_month = {}
            for r in new:
                by_month.setdefault(r["ts"][:7], []).append(r)
            for m, rows in by_month.items():
                with open(raw_dir / f"{m}.jsonl", "a", encoding="utf-8") as f:
                    for r in rows:
                        f.write(json.dumps(r) + "\n")
        print(f"feed rows: {len(events)}, new: {len(new)}")

    # ---------------- load full archive, re-geocode from loc ----------------
    gc_cache = {}

    def gcode(loc):
        v = gc_cache.get(loc)
        if v is None:
            v = gc_cache[loc] = geo.geocode(loc)
        return v

    archive = []
    for f in sorted(raw_dir.glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except Exception:
                continue
            lon, lat, gq = gcode(r["loc"])
            r["lon"], r["lat"], r["gq"] = lon, lat, gq
            r["cat"] = categorize(r["type"])           # re-bucket under the current taxonomy
            archive.append(r)

    iso = lambda days: (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    iso24, iso7d, iso30 = iso(1), iso(7), iso(30)

    def point(r, full=True):
        props = {"t": r["type"].title(), "c": r["cat"], "ts": r["ts"],
                 "loc": r["loc"].title(), "gq": r["gq"], "sens": r.get("sens", 0)}
        if not full:
            props = {"c": r["cat"]}
        return {"type": "Feature",
                "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
                "properties": props}

    placed = [r for r in archive if r["lon"] is not None]

    def dump(name, feats):
        (out_dir / name).write_text(
            json.dumps({"type": "FeatureCollection", "features": feats}), encoding="utf-8")

    dump("recent_24h.geojson", [point(r) for r in placed if r["ts"] >= iso24])
    dump("recent_7d.geojson", [point(r, full=False) for r in placed if r["ts"] >= iso7d])
    dump("all.geojson", [point(r) for r in placed])

    # ~500 ground-ft grid over the last 30 days, per-category counts
    lat0 = 34.75
    cell = GRID_FT * 0.3048 / math.cos(math.radians(lat0))
    cells = {}
    for r in placed:
        if r["ts"] < iso30:
            continue
        x, y = _merc(r["lon"], r["lat"])
        key = (int(x // cell), int(y // cell))
        c = cells.setdefault(key, {k: 0 for k in CAT_KEYS})
        c[r["cat"]] = c.get(r["cat"], 0) + 1
    feats = []
    for (cx, cy), counts in cells.items():
        x0, y0 = cx * cell, cy * cell
        ring = [_unmerc(x0, y0), _unmerc(x0 + cell, y0), _unmerc(x0 + cell, y0 + cell),
                _unmerc(x0, y0 + cell), _unmerc(x0, y0)]
        n = sum(counts.values())
        top = sorted(((v, k) for k, v in counts.items() if v), reverse=True)[:3]
        feats.append({"type": "Feature",
                      "geometry": {"type": "Polygon",
                                   "coordinates": [[[round(a, 6), round(b, 6)] for a, b in ring]]},
                      "properties": {"n": n, **counts,
                                     "top": ", ".join(f"{k} ({v})" for v, k in top)}})
    dump("grid_30d.geojson", feats)

    # ---------------- stats ----------------
    from collections import Counter
    total = len(archive)
    placed_n = len(placed)
    gq_counts = Counter(r["gq"] for r in archive)
    cat_counts = Counter(r["cat"] for r in placed)
    earliest = min((r["ts"] for r in archive), default=None)
    (out_dir / "stats.json").write_text(json.dumps({
        "updated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_collected": total,
        "collecting_since": earliest,
        "placed": placed_n,
        "geocode_rate": round(placed_n / max(1, total), 3),
        "geocode_quality": dict(gq_counts),
        "by_category": dict(cat_counts),
        "last_24h": sum(1 for r in placed if r["ts"] >= iso24),
        "last_7d": sum(1 for r in placed if r["ts"] >= iso7d),
        "last_30d": sum(1 for r in placed if r["ts"] >= iso30),
    }), encoding="utf-8")
    print(f"outputs: all-time={placed_n}/{total} placed ({100*placed_n/max(1,total):.0f}%), "
          f"24h={sum(1 for r in placed if r['ts'] >= iso24)}, grid cells={len(feats)}; "
          f"quality={dict(gq_counts)}")


if __name__ == "__main__":
    main()
