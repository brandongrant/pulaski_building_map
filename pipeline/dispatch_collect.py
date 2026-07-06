"""Collect Little Rock public dispatch events and publish map-ready outputs.

Designed to run on a GitHub Actions runner (stdlib + requests only).

Usage: python pipeline/dispatch_collect.py --store <data-branch-checkout-dir>

Store layout:
  dispatch/address_index.json.gz   normalized address -> [lon, lat] (+ street points)
  dispatch/raw/YYYY-MM.jsonl       append-only archive of unique events
  dispatch/out/recent_24h.geojson  point layer (sensitive categories excluded)
  dispatch/out/recent_7d.geojson   bare points for heatmap (no identifying props)
  dispatch/out/grid_30d.geojson    ~500 ft grid cells with per-category counts
  dispatch/out/stats.json          totals + collection metadata
"""
import argparse
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

URL = "https://web.littlerock.state.ar.us/pub/Home/CadEvents"
JURISDICTION = "Little Rock"
LOCAL_TZ = ZoneInfo("America/Chicago")
GRID_FT = 500.0

CATEGORY_RULES = [
    ("Alarm", r"ALARM"),
    ("Person/Welfare", r"MEDICAL|CONDITION|SUBJECT DOWN|WELFARE|MISSING|SUICID|OVERDOSE|DEATH|MENTAL|INTOX"),
    ("Traffic", r"ACCIDENT|TRAFFIC|STALL|PARKING|BLOCKED|ABANDONED VEH|WRECKER|MOTORIST"),
    ("Property", r"THEFT|BURGLAR|STOLEN|CRIMINAL MISCHIEF|SHOPLIFT|FRAUD|ROBBERY|BREAKING|VANDAL"),
    ("Disturbance", r"DISTURBANCE|LOITER|NOISE|FIGHT|SHOTS|FIREWORK|THREAT|HARASS|DRUNK"),
    ("Animal", r"ANIMAL|VICIOUS|DOG BITE"),
    ("Suspicious", r"SUSPICIOUS|PROWLER|UNKNOWN"),
    ("Administrative", r"ADMIN|INFORMATION|PRISONER|TRANSPORT|ESCORT|RELAY|ASSIST|STANDBY|REPORT"),
]
SENSITIVE_RE = re.compile(
    r"MEDICAL|DEATH|SUBJECT DOWN|CONDITION|WELFARE|SUICID|OVERDOSE|MENTAL|JUVENILE|RAPE|SEX|DOMESTIC")
CAT_KEYS = {"Alarm": "al", "Traffic": "tr", "Property": "pr", "Disturbance": "di",
            "Person/Welfare": "pw", "Suspicious": "su", "Animal": "an",
            "Administrative": "ad", "Other": "ot"}

NONALNUM = re.compile(r"[^A-Z0-9 /]+")
WS = re.compile(r"\s+")
SUFFIXES = r"(?: (?:ST|AVE?|RD|DR|LN|CT|CIR|BLVD|PL|WAY|HWY|PIKE|CV|TRL|TER|LOOP|PKWY|EXPY|FRONTAGE))?"


def categorize(t):
    for cat, pat in CATEGORY_RULES:
        if re.search(pat, t):
            return cat
    return "Other"


def norm(s):
    return WS.sub(" ", NONALNUM.sub(" ", s.upper())).strip()


class Geocoder:
    def __init__(self, index_path):
        with gzip.open(index_path, "rt", encoding="utf-8") as f:
            d = json.load(f)
        self.addr = d["addr"]        # "708 MAIN ST" and "708 MAIN" -> [lon, lat]
        self.streets = d["streets"]  # "MAIN ST" / "MAIN" -> [[lon, lat], ...]

    def _street_pts(self, name):
        name = name.strip()
        if name in self.streets:
            return self.streets[name]
        # drop a trailing type ("KANIS RD" -> "KANIS")
        parts = name.split(" ")
        if len(parts) > 1 and " ".join(parts[:-1]) in self.streets:
            return self.streets[" ".join(parts[:-1])]
        return None

    def geocode(self, loc):
        """-> (lon, lat, quality) or (None, None, 'failed')"""
        q = norm(loc)
        if "/" in q:  # intersection
            a, _, b = q.partition("/")
            pa, pb = self._street_pts(a), self._street_pts(b)
            if pa and pb:
                best, bd = None, 1e18
                for x1, y1 in pa[:400]:
                    for x2, y2 in pb[:400]:
                        d = (x1 - x2) ** 2 + (y1 - y2) ** 2
                        if d < bd:
                            bd, best = d, ((x1 + x2) / 2, (y1 + y2) / 2)
                if best and bd < (0.01 ** 2):  # ~1 km sanity
                    return best[0], best[1], "intersection"
            return None, None, "failed"
        if q in self.addr:
            lon, lat = self.addr[q]
            return lon, lat, "exact_address"
        m = re.match(r"^(\d+) (.+)$", q)
        if m:
            num, street = int(m.group(1)), m.group(2)
            # nearest house number on the same street (block-level interpolation)
            pts = []
            for cand in (street, " ".join(street.split(" ")[:-1])):
                if not cand:
                    continue
                pref = re.compile(r"^(\d+) " + re.escape(cand) + "$")
                # exact-street scan is too slow over the whole dict; use street index
                sp = self._street_pts(cand)
                if sp:
                    pts = sp
                    break
            if pts:
                # no house-number data in street index; use street midpoint
                lon = sum(p[0] for p in pts) / len(pts)
                lat = sum(p[1] for p in pts) / len(pts)
                return lon, lat, "road_segment"
        sp = self._street_pts(q)
        if sp:
            lon = sum(p[0] for p in sp) / len(sp)
            lat = sum(p[1] for p in sp) / len(sp)
            return lon, lat, "road_segment"
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    args = ap.parse_args()
    store = Path(args.store) / "dispatch"
    raw_dir = store / "raw"
    out_dir = store / "out"
    raw_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    geo = Geocoder(store / "address_index.json.gz")
    now = datetime.now(timezone.utc)

    # seen-set from current + previous month archives (feed lags up to ~8 h)
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
        lon, lat, quality = geo.geocode(loc)
        cat = categorize(t)
        new.append({
            "id": eid, "src": "CLR-CAD", "jur": JURISDICTION,
            "type": t, "cat": cat, "loc": loc,
            "ts": dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "lon": None if lon is None else round(lon, 6),
            "lat": None if lat is None else round(lat, 6),
            "gq": quality,
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

    # ---------------- rebuild outputs from archive ----------------
    horizon = now - timedelta(days=31)
    recent = []
    for f in sorted(raw_dir.glob("*.jsonl")):
        if f.stem < horizon.strftime("%Y-%m"):
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r["ts"] >= horizon.strftime("%Y-%m-%dT%H:%M:%SZ"):
                recent.append(r)

    iso24 = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    iso7d = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    iso30 = (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")

    pts24 = [{"type": "Feature",
              "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
              "properties": {"t": r["type"].title(), "c": r["cat"], "ts": r["ts"],
                             "loc": r["loc"].title(), "gq": r["gq"]}}
             for r in recent
             if r["ts"] >= iso24 and r["lon"] is not None and not r["sens"]]
    (out_dir / "recent_24h.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": pts24}), encoding="utf-8")

    pts7 = [{"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
             "properties": {"c": r["cat"]}}
            for r in recent if r["ts"] >= iso7d and r["lon"] is not None]
    (out_dir / "recent_7d.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": pts7}), encoding="utf-8")

    # ~500 ground-ft grid in web-mercator
    lat0 = 34.75
    cell = GRID_FT * 0.3048 / math.cos(math.radians(lat0))
    R = 6378137.0

    def merc(lon, lat):
        return (math.radians(lon) * R,
                R * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))

    def unmerc(x, y):
        return (math.degrees(x / R),
                math.degrees(2 * math.atan(math.exp(y / R)) - math.pi / 2))

    cells = {}
    for r in recent:
        if r["ts"] < iso30 or r["lon"] is None:
            continue
        x, y = merc(r["lon"], r["lat"])
        key = (int(x // cell), int(y // cell))
        c = cells.setdefault(key, {k: 0 for k in CAT_KEYS.values()})
        c[CAT_KEYS[r["cat"]]] += 1
    feats = []
    for (cx, cy), counts in cells.items():
        x0, y0 = cx * cell, cy * cell
        ring = [unmerc(x0, y0), unmerc(x0 + cell, y0), unmerc(x0 + cell, y0 + cell),
                unmerc(x0, y0 + cell), unmerc(x0, y0)]
        n = sum(counts.values())
        top = sorted(((v, k) for k, v in counts.items() if v), reverse=True)[:3]
        rev = {v: k for k, v in CAT_KEYS.items()}
        feats.append({"type": "Feature",
                      "geometry": {"type": "Polygon",
                                   "coordinates": [[[round(a, 6), round(b, 6)] for a, b in ring]]},
                      "properties": {"n": n, **counts,
                                     "top": ", ".join(f"{rev[k]} ({v})" for v, k in top)}})
    (out_dir / "grid_30d.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": feats}), encoding="utf-8")

    total = 0
    earliest = None
    for f in sorted(raw_dir.glob("*.jsonl")):
        lines = f.read_text(encoding="utf-8").splitlines()
        total += len(lines)
        if lines and earliest is None:  # oldest month; feed order is newest-first
            try:
                earliest = min(json.loads(x)["ts"] for x in lines)
            except Exception:
                pass
    geocoded = sum(1 for r in recent if r["lon"] is not None)
    (out_dir / "stats.json").write_text(json.dumps({
        "updated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_collected": total,
        "collecting_since": earliest,
        "last_24h": len(pts24),
        "last_7d": len(pts7),
        "last_30d": sum(1 for r in recent if r["ts"] >= iso30),
        "geocode_rate_30d": round(geocoded / max(1, len(recent)), 3),
    }), encoding="utf-8")
    print(f"outputs: 24h={len(pts24)} pts, 7d={len(pts7)} pts, grid cells={len(feats)}, "
          f"total archive={total}")


if __name__ == "__main__":
    main()
