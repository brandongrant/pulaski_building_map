"""Reduce every crime-ish dataset the project collects into one small summary.

The site's Pulse tab has to answer "what is happening around the city, and
when" the instant it opens, on a phone, without downloading a 3 MB GeoJSON and
grinding through it in JavaScript. So the arithmetic happens here, in the same
scheduled job that collects the data, and the browser gets a single compact
file (a few hundred KB) of pre-rolled aggregates.

Inputs
  <store>/dispatch/out/all.geojson    every geocoded call for service, all-time
  <store>/reports/out/incidents.json  parsed LRPD daily incident reports
  web/data/crime/crimes.json          LRPD reported index offenses, 2017-2025

Output
  <store>/pulse/out/pulse.json

Everything is bucketed in America/Chicago local time — "3 a.m." has to mean
3 a.m. in Little Rock, not UTC.

Usage: python pipeline/build_pulse.py --store <data-branch-checkout-dir>
"""
import argparse
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from common.settings import WEB_DATA_DIR

LOCAL_TZ = ZoneInfo("America/Chicago")
# Hex mosaic cell size: the circumradius in Web-Mercator metres, so a cell is
# 2*HEX_M wide and sqrt(3)*HEX_M tall. Mercator metres at Little Rock's latitude
# are about 0.82 of a ground metre, so 500 here is roughly a 1/4-mile cell.
HEX_M = 500.0
CLOCK_DAYS = 90           # rolling window for the hour-of-day clock
WEEKS = 14                # trend history length
DAY_SERIES = 60           # daily sparkline length
TOP_STREETS = 28
TOP_TYPES = 24
CASE_FILES = 120          # most recent daily-report incidents shipped to the UI

# Categories that describe a crime rather than an errand. The clock, the mosaic
# and the leaderboard use this set so "assist / admin" traffic (a third of all
# calls) cannot drown out what people actually want to see; everything is still
# counted in the totals.
CRIME_CATS = ["shots", "assault", "robbery", "burglary", "theft", "fraud",
              "vandalism", "drugs", "domestic", "sex", "juvenile", "trespass",
              "disturbance", "suspicious", "traffic", "alarm", "animal",
              "welfare", "assist", "other"]
FOCUS_CATS = CRIME_CATS[:14]     # through "disturbance": the reportable stuff

_R = 6378137.0
UNIT_RE = re.compile(r"\b(APT|UNIT|STE|SUITE|BLDG|LOT|RM|#)\b.*$")
HOUSE_RE = re.compile(r"^\d+[A-Z]?\s+")


def merc(lon, lat):
    return (math.radians(lon) * _R,
            _R * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))


def hex_cell(x, y, size):
    """Point -> flat-top hex axial coordinates (q, r)."""
    q = (2 / 3 * x) / size
    r = (-1 / 3 * x + math.sqrt(3) / 3 * y) / size
    # cube rounding so points land in exactly one hex
    xc, zc = q, r
    yc = -xc - zc
    rx, ry, rz = round(xc), round(yc), round(zc)
    dx, dy, dz = abs(rx - xc), abs(ry - yc), abs(rz - zc)
    if dx > dy and dx > dz:
        rx = -ry - rz
    elif dy > dz:
        ry = -rx - rz
    else:
        rz = -rx - ry
    return int(rx), int(rz)


def street_of(loc):
    """'2317 S CEDAR ST APT 4' -> 'S CEDAR ST'; intersections keep both names."""
    loc = " ".join((loc or "").upper().split())
    if not loc:
        return None
    part = loc.split("/")[0].strip() if "/" in loc else loc
    part = HOUSE_RE.sub("", part)
    part = UNIT_RE.sub("", part).strip()
    part = re.sub(r"\s+\d+$", "", part).strip()
    return part if len(part) > 3 else None


def load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--out", default=None, help="override the output path")
    args = ap.parse_args()
    store = Path(args.store)
    now = datetime.now(timezone.utc)
    now_local = now.astimezone(LOCAL_TZ)

    # ---------------------------------------------------------------- calls --
    calls = load_json(store / "dispatch" / "out" / "all.geojson",
                      {"features": []})["features"]
    dsp_stats = load_json(store / "dispatch" / "out" / "stats.json", {}) or {}

    clock = {c: [0] * 24 for c in CRIME_CATS}
    dow_hour = [[0] * 24 for _ in range(7)]
    dow_hour_cat = {c: [[0] * 24 for _ in range(7)] for c in CRIME_CATS}
    week_cat = defaultdict(Counter)
    hexes = defaultdict(Counter)
    streets = defaultdict(Counter)
    types = Counter()
    type_cat = {}
    totals = Counter()
    day_counts = Counter()
    days = defaultdict(Counter)
    cat_7d, cat_prev7 = Counter(), Counter()
    latest_ts = ""

    cut_clock = now - timedelta(days=CLOCK_DAYS)
    cut_weeks = now - timedelta(weeks=WEEKS)
    cut = {k: now - timedelta(days=d) for k, d in (("d1", 1), ("d7", 7), ("d30", 30))}
    prev7_lo, prev7_hi = now - timedelta(days=14), now - timedelta(days=7)

    for f in calls:
        p = f.get("properties") or {}
        ts = p.get("ts") or ""
        cat = p.get("c") or "other"
        if cat not in clock:
            cat = "other"
        try:
            t = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        local = t.astimezone(LOCAL_TZ)
        latest_ts = max(latest_ts, ts)
        totals["all"] += 1
        day = local.strftime("%Y-%m-%d")
        day_counts[day] += 1
        days[day][cat] += 1
        for k, c in cut.items():
            if t >= c:
                totals[k] += 1
        if t >= cut["d7"]:
            cat_7d[cat] += 1
        if prev7_lo <= t < prev7_hi:
            totals["prev7"] += 1
            cat_prev7[cat] += 1

        if t >= cut_clock:
            clock[cat][local.hour] += 1
            dow_hour[local.weekday()][local.hour] += 1
            if cat in dow_hour_cat:
                dow_hour_cat[cat][local.weekday()][local.hour] += 1
        if t >= cut_weeks:
            week_cat[local.strftime("%G-W%V")][cat] += 1

        typ = (p.get("t") or "").strip()
        if typ:
            types[typ] += 1
            type_cat.setdefault(typ, cat)
        st = street_of(p.get("loc"))
        if st:
            streets[st][cat] += 1
            streets[st]["_n"] += 1

        lon, lat = ((f.get("geometry") or {}).get("coordinates") or (None, None))[:2]
        if lon is not None:
            q, r = hex_cell(*merc(lon, lat), HEX_M)
            hexes[(q, r)][cat] += 1
            hexes[(q, r)]["_n"] += 1

    # order categories by how much of the recent picture they actually are
    cat_totals = Counter()
    for c in CRIME_CATS:
        cat_totals[c] = sum(clock[c])
    cat_order = [c for c, _ in cat_totals.most_common() if cat_totals[c]]

    weeks = sorted(week_cat)[-WEEKS:]
    # today is still in progress — a half-height bar at the end of the daily
    # series reads as a collapse in crime rather than as "it is 7 a.m."
    today = now_local.strftime("%Y-%m-%d")
    day_labels = [d for d in sorted(day_counts) if d != today][-DAY_SERIES:]
    # Cells carry their whole category breakdown, indexed into cat_order, so the
    # mosaic can be re-coloured for any single category without another fetch.
    cat_ix = {c: i for i, c in enumerate(cat_order)}
    hex_cells = []
    for (q, r), c in sorted(hexes.items(), key=lambda kv: -kv[1]["_n"]):
        parts = sorted(((v, cat_ix[k]) for k, v in c.items()
                        if k != "_n" and k in cat_ix), reverse=True)
        if not parts:
            continue
        hex_cells.append([q, r, c["_n"], [[i, v] for v, i in parts]])
    top_streets = []
    for n, s, c in sorted(((c["_n"], s, c) for s, c in streets.items()), reverse=True)[:TOP_STREETS]:
        parts = sorted(((v, cat_ix[k]) for k, v in c.items()
                        if k != "_n" and k in cat_ix), reverse=True)
        top_streets.append([s, n, [[i, v] for v, i in parts[:6]]])

    # -------------------------------------------------------------- reports --
    rep = load_json(store / "reports" / "out" / "incidents.json", {}) or {}
    rep_stats = load_json(store / "reports" / "out" / "stats.json", {}) or {}
    cases = []
    for r in (rep.get("incidents") or [])[:CASE_FILES]:
        cases.append({k: r.get(k) for k in (
            "no", "date", "date_exact", "dt", "cat", "call_type_label",
            "offenses", "district", "loc", "tags", "lon", "lat",
            "url", "pdf_page", "parsed")})
    tag_counts = Counter(t for r in (rep.get("incidents") or [])
                         for t in (r.get("tags") or []))

    # -------------------------------------------------------------- history --
    hist = load_json(WEB_DATA_DIR / "crime" / "crimes.json")
    history = None
    if hist:
        off_cat = hist.get("off_cat") or []
        ym_cat = Counter()
        months_seen = defaultdict(set)
        for lon, lat, oi, ymd, *_ in hist["crime"]:
            cat = off_cat[oi] if oi < len(off_cat) else "other"
            year, month = ymd // 10000, (ymd // 100) % 100
            ym_cat[(cat, year, month)] += 1
            months_seen[year].add(month)
        years = sorted(months_seen)
        # "…to year to date" exports end mid-year. A year that is missing months
        # must not be drawn next to full ones, and the month-of-year profile has
        # to be built from whole years or every month the export stopped short of
        # looks quiet.
        full_years = [y for y in years if len(months_seen[y]) == 12]
        cats = sorted({c for c, _, _ in ym_cat})
        history = {
            "years": years,
            "full_years": full_years,
            "by_year": {c: [sum(ym_cat[(c, y, m)] for m in range(1, 13)) for y in years]
                        for c in cats},
            "by_month": {c: [sum(ym_cat[(c, y, m)] for y in full_years)
                             for m in range(1, 13)] for c in cats},
            "total": hist.get("count"),
        }

    out = {
        "updated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "updated_local": now_local.strftime("%Y-%m-%dT%H:%M"),
        "tz": "America/Chicago",
        "calls": {
            "since": dsp_stats.get("collecting_since"),
            "latest": latest_ts,
            "total": totals["all"],
            "d1": totals["d1"], "d7": totals["d7"], "d30": totals["d30"],
            "prev7": totals["prev7"],
            "days_collected": len(day_counts),
            "busiest_day": max(day_counts.items(), key=lambda kv: kv[1],
                               default=(None, 0)),
            "clock_days": CLOCK_DAYS,
            "by_cat_7d": dict(cat_7d),
            "by_cat_prev7": dict(cat_prev7),
        },
        "days": {"labels": day_labels,
                 "total": [day_counts[d] for d in day_labels],
                 "by_cat": {c: [days[d].get(c, 0) for d in day_labels]
                            for c in cat_order}},
        "cat_order": cat_order,
        "focus_cats": FOCUS_CATS,
        "clock": {c: clock[c] for c in cat_order},
        "dow_hour": dow_hour,
        "dow_hour_cat": {c: v for c, v in dow_hour_cat.items() if any(map(sum, v))},
        "weeks": {"labels": weeks,
                  "by_cat": {c: [week_cat[w].get(c, 0) for w in weeks]
                             for c in cat_order}},
        # Axial (q, r) only — the browser lays the mosaic out itself and can
        # invert it to a real coordinate when a cell is clicked:
        #   x = size*1.5*q ; y = size*(sqrt(3)/2*q + sqrt(3)*r)  (Web Mercator m)
        "hex": {"size_m": HEX_M, "cells": hex_cells},
        "streets": top_streets,
        "types": [[t, n, type_cat.get(t, "other")] for t, n in types.most_common(TOP_TYPES)],
        "reports": {
            "collected": rep_stats.get("reports_collected", 0),
            "incidents": rep_stats.get("incidents", 0),
            "placed": rep_stats.get("placed", 0),
            "first": rep_stats.get("first_report"),
            "last": rep_stats.get("last_report"),
            "tag_labels": rep.get("tag_labels", {}),
            "tag_counts": dict(tag_counts),
            "cases": cases,
        },
        "history": history,
    }

    out_path = Path(args.out) if args.out else store / "pulse" / "out" / "pulse.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    kb = out_path.stat().st_size / 1024
    print(f"pulse.json: {kb:.0f} KB — {totals['all']} calls over "
          f"{len(day_counts)} days, {len(hex_cells)} mosaic cells, "
          f"{len(cases)} case files, "
          f"history {'yes' if history else 'MISSING'}")


if __name__ == "__main__":
    main()
