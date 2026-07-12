"""Collect Little Rock 311 service requests and publish map-ready outputs.

Designed to run on a GitHub Actions runner (stdlib + requests only), beside
dispatch_collect.py in the same cron. Source: the city's Motorola CWI citizen
portal (littlerock-cwiprod.motorolasolutions.com). Its list API is public,
needs no session, and serves everything UPDATED in the last ~30 days
(~16k rows), newest-updated first, 500 rows per page. There is no deeper
archive (the by-number endpoint covers the same window), so history
accumulates from the first collect onward — same model as dispatch.

Hard-won API facts (measured 2026-07-11, do not re-derive):
  - GET /api/srstatus/list/<MMDDYYYY>?filter=&pnum=N&psize=500&count=y
    The date path segment is ignored (the SPA passes today's date as a
    cache key); filter is free text; ordering is updated_date DESC.
  - `updated_date` claims Z but is actually America/Chicago local time.
  - List rows have no created/closed dates and no coordinates; the detail
    endpoint (api/apphub/srdatabynumber/<n>) adds XY but still no dates, so
    dates are OBSERVED here instead:
      opened: a request's first archived version has an open-class status
              only when that update WAS its creation (an old untouched
              request has an old updated_date and can't enter the window),
              so first-seen-open => opened = that updated_date.
      closed: status transitions are new updated_dates; the fold records
              the update that moved the row into a closed-class status.
  - prc numbers are per-year sequential ("26-00096926"); blacklisted rows
    ("Y") are spam/abuse flagged by the city and are skipped.

Usage: python pipeline/sr311_collect.py --store <data-branch-checkout-dir>
       [--seed] [--max-pages N] [--rebuild-only]

Store layout (shares the root with dispatch/):
  dispatch/address_index.json.gz  shared geocoder index (built once)
  sr311/raw/YYYY-MM.jsonl         append-only archive of observed versions,
                                  one line per (number, updated) first seen
  sr311/out/requests.geojson      one point per geocodable request
  sr311/out/stats.json            totals + collection metadata
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from dispatch_collect import Geocoder

BASE = "https://littlerock-cwiprod.motorolasolutions.com"
LOCAL_TZ = ZoneInfo("America/Chicago")
PSIZE = 500

# category key -> (label, desc-matching regex). First match wins; keys are
# what the web app colors/filters by, so keep them short and stable.
CATEGORY_RULES = [
    ("san", "Sanitation", r"GARBAGE|RECYCLING|YARD WASTE|BULKY|SPECIAL PICK|APPLIANCE|DECEASED ANIMAL|CART"),
    ("code", "Code enforcement", r"HIGH GRASS|DEBRIS ON PREMISE|HOUSING CODE|PARKING IN YARD|INOPERABLE VEHICLE|ZONING|LANDSCAPING VIOL|SIGN CODE|RENTAL INSPECT"),
    ("traffic", "Traffic & lights", r"STREET LIGHT|TRAFFIC|STREET NAME SIGN|SIGHT OBSTRUCTION|LIGHTS OUT"),
    ("street", "Streets & drainage", r"POTHOLE|SIDEWALK|CURB|DITCH|FLOOD|CATCH BASIN|DRAIN|MANHOLE|ALLEY|SWEEPING|MOWING|LITTER|HAZARD|GRAFFITI|SIGN REMOVAL|ACCESS RAMP|EROSION"),
    ("animal", "Animals", r"ANIMAL|DOG|STRAY|TRAP REQUEST|VACCINATION"),
    ("park", "Parks", r"PARK|PLAYGROUND|PAVILLION|RESTROOM|VANDALISM"),
    ("tree", "Trees", r"TREE"),
    ("constr", "Construction", r"WORKING (WITHOUT|AFTER)|CONSTRUCTION"),
]
CAT_LABELS = {k: lbl for k, lbl, _ in CATEGORY_RULES}
CAT_LABELS["oth"] = "Other"

OPEN_STATUSES = {"OPEN", "DUPLICATE (OPEN)"}
CLOSED_RE = re.compile(r"CLOSED|DUPLICATE \(CLOSED\)|RESOLVED|COMPLETED|CANCEL")


def categorize(desc):
    d = desc.upper()
    for key, _lbl, pat in CATEGORY_RULES:
        if re.search(pat, d):
            return key
    return "oth"


def status_class(desc):
    """open / closed / sent (routed to a utility, no further city updates)"""
    d = desc.upper()
    if CLOSED_RE.search(d):
        return "c"
    if d.startswith("SENT TO"):
        return "s"
    return "o"


def parse_updated(s):
    """CWI updated_date: ISO-with-Z text that is really America/Chicago."""
    dt = datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=LOCAL_TZ)
    return dt.astimezone(timezone.utc)


def split_address(display):
    """'5 NEWBRIDGE CT, LITTLE ROCK, AR 72227' -> ('5 NEWBRIDGE CT', 'LITTLE ROCK')"""
    parts = [p.strip() for p in str(display or "").split(",")]
    street = parts[0].upper() if parts else ""
    city = parts[1].upper() if len(parts) > 2 else ""
    return street, city


def fetch_page(sess, pnum, want_count):
    stamp = datetime.now(LOCAL_TZ).strftime("%m%d%Y")
    url = (f"{BASE}/api/srstatus/list/{stamp}?filter=&pnum={pnum}"
           f"&psize={PSIZE}&count={'y' if want_count else 'n'}")
    last = None
    for i in range(4):
        try:
            r = sess.get(url, timeout=90, headers={"Accept": "application/json"})
            r.raise_for_status()
            d = r.json()
            rows = d["data"]["results"]["service_requests"]
            return rows, d.get("count")
        except Exception as e:
            last = e
            print(f"page {pnum} retry {i + 1}: {e}", file=sys.stderr)
            time.sleep(8 * (i + 1))
    raise RuntimeError(f"could not fetch 311 list page {pnum}: {last}")


def load_archive(raw_dir):
    """-> (rows sorted by updated, set of (number, updated) keys)"""
    rows, keys = [], set()
    for f in sorted(raw_dir.glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except Exception:
                continue
            k = (r["n"], r["u"])
            if k in keys:
                continue
            keys.add(k)
            rows.append(r)
    rows.sort(key=lambda r: (r["n"], r["u"]))
    return rows, keys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--seed", action="store_true",
                    help="walk the whole ~30-day window (first run)")
    ap.add_argument("--max-pages", type=int, default=6)
    ap.add_argument("--rebuild-only", action="store_true",
                    help="no network; regenerate outputs from the archive")
    args = ap.parse_args()

    root = Path(args.store)
    store = root / "sr311"
    raw_dir = store / "raw"
    out_dir = store / "out"
    raw_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    archive, seen = load_archive(raw_dir)
    watermark = max((r["u"] for r in archive), default="")

    # ---------------- collect ----------------
    new_rows = []
    if not args.rebuild_only:
        sess = requests.Session()
        max_pages = 40 if args.seed else args.max_pages
        total_count = None
        for pnum in range(1, max_pages + 1):
            rows, count = fetch_page(sess, pnum, want_count=(pnum == 1))
            if pnum == 1 and count:
                total_count = count
            page_new = 0
            page_min_u = None
            for row in rows:
                if str(row.get("blacklisted", "N")).upper() == "Y":
                    continue
                n = str(row.get("prc_number", "")).strip()
                upd = row.get("updated_date")
                if not n or not upd:
                    continue
                try:
                    u = parse_updated(upd).strftime("%Y-%m-%dT%H:%M:%SZ")
                except ValueError:
                    continue
                page_min_u = u if page_min_u is None else min(page_min_u, u)
                if (n, u) in seen:
                    continue
                seen.add((n, u))
                street, city = split_address(row.get("display_address"))
                ty = str(row.get("prc_desc", "")).strip()
                sd = str(row.get("status_desc", "")).strip()
                rec = {
                    "n": n, "u": u,
                    "ty": ty, "t": categorize(ty),
                    "sd": sd, "s": status_class(sd),
                    "addr": street, "city": city,
                    "ch": str(row.get("channel_desc", "")).strip(),
                    "seen": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
                new_rows.append(rec)
                page_new += 1
            done = len(rows) < PSIZE                       # past the last page
            if not args.seed and page_new == 0 and watermark:
                done = True                                # caught up to archive
            if not args.seed and watermark and page_min_u and page_min_u < watermark:
                done = True                                # walked past the watermark
            if done:
                break
            time.sleep(1.5)                                # politeness between pages
        print(f"311 window={total_count} pages={pnum} new versions={len(new_rows)}")

        if new_rows:
            by_month = {}
            for r in new_rows:
                by_month.setdefault(r["u"][:7], []).append(r)
            for m, rows in by_month.items():
                with open(raw_dir / f"{m}.jsonl", "a", encoding="utf-8") as f:
                    for r in rows:
                        f.write(json.dumps(r) + "\n")
            archive.extend(new_rows)
            archive.sort(key=lambda r: (r["n"], r["u"]))

    # ---------------- fold versions per request ----------------
    reqs = {}
    for r in archive:
        q = reqs.get(r["n"])
        if q is None:
            q = reqs[r["n"]] = {
                "n": r["n"], "first": r, "opened": None, "closed": None,
            }
            # first-ever observed version with an open-class status can only
            # be the creation update (see module docstring)
            if r["sd"].upper() in OPEN_STATUSES:
                q["opened"] = r["u"]
        q["last"] = r
        if r["s"] == "c":
            if q["closed"] is None or r["u"] > q["closed"]:
                q["closed"] = r["u"]
        elif q["closed"] is not None and r["u"] > q["closed"]:
            q["closed"] = None                             # reopened later

    # ---------------- geocode + geojson ----------------
    geo = Geocoder(root / "dispatch" / "address_index.json.gz")
    feats = []
    geocoded = 0
    day = lambda iso: int(iso[:10].replace("-", "")) if iso else None
    for q in reqs.values():
        last = q["last"]
        lon, lat, quality = geo.geocode(last["addr"]) if last["addr"] else (None, None, "failed")
        if lon is None:
            continue
        geocoded += 1
        p = {
            "n": q["n"], "t": last["t"], "ty": last["ty"],
            "s": last["s"], "sd": last["sd"],
            "u": day(last["u"]), "ch": last["ch"],
            "a": last["addr"],                             # already ADRLABEL-style
            "gq": quality,
        }
        if q["opened"]:
            p["o"] = day(q["opened"])
        if q["closed"]:
            p["cl"] = day(q["closed"])
        if last["city"] and last["city"] != "LITTLE ROCK":
            p["c"] = last["city"]
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
            "properties": p,
        })
    feats.sort(key=lambda f: f["properties"].get("u") or 0, reverse=True)
    (out_dir / "requests.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": feats}), encoding="utf-8")

    # ---------------- stats ----------------
    by_cat = {}
    open_now = 0
    for q in reqs.values():
        by_cat[q["last"]["t"]] = by_cat.get(q["last"]["t"], 0) + 1
        if q["last"]["s"] == "o":
            open_now += 1
    earliest = min((r["u"] for r in archive), default=None)
    (out_dir / "stats.json").write_text(json.dumps({
        "updated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_requests": len(reqs),
        "total_versions": len(archive),
        "collecting_since": earliest,
        "open_now": open_now,
        "with_opened_date": sum(1 for q in reqs.values() if q["opened"]),
        "with_closed_date": sum(1 for q in reqs.values() if q["closed"]),
        "geocode_rate": round(geocoded / max(1, len(reqs)), 3),
        "by_category": by_cat,
        "category_labels": CAT_LABELS,
    }), encoding="utf-8")
    print(f"311 outputs: {len(feats)} points / {len(reqs)} requests "
          f"({geocoded / max(1, len(reqs)):.0%} geocoded), open now {open_now}")


if __name__ == "__main__":
    main()
