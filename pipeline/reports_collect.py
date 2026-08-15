"""Collect the City of Little Rock daily police reports and publish them.

The city posts a PDF each weekday (by noon) holding a few complete LRPD
incident-report packets, and keeps only about a week of them online — older
dates 404. So the archive has to be built by checking in regularly, exactly
like the dispatch collector: this script scrapes the listing page, downloads
report dates it has not seen, parses each PDF (``lrpd_reports``), and appends
the incident records to an append-only JSONL archive on the ``data`` branch.

Usage: python pipeline/reports_collect.py --store <data-branch-checkout-dir>

Store layout:
  reports/raw/YYYY-MM.jsonl      append-only archive, one row per incident
  reports/seen.json              report dates already downloaded (+ page counts)
  reports/state.json             last check time, for the interval gate
  reports/out/incidents.json     published records, geocoded, newest first
  reports/out/stats.json         totals + parse/geocode quality

The workflow that hosts this runs hourly for dispatch, so the collector
self-throttles with ``--min-interval-hours`` (default 4): it fetches the city's
listing page only a handful of times a day and exits immediately otherwise.
Pass ``--force`` to ignore the gate.

Privacy: only incident-level fields are archived — number, time, call type,
statutory offenses, district, address. Report narratives name victims,
witnesses and suspects; they are read to derive mechanical tags (weapon,
forced entry, property taken…) and then discarded. Nothing person-level is
written to disk. Each record keeps a link back to the city's own PDF page so
the full official document stays one click away.
"""
import argparse
import io
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

import lrpd_reports
from dispatch_collect import Geocoder, categorize

PAGE_URL = ("https://littlerock.gov/residents/police-department/"
            "21st-century-policing/daily-reports/")
PDF_RE = re.compile(r"https://littlerock\.gov/wp-content/uploads/"
                    r"(\d{2})-(\d{2})-(20\d\d)\.pdf", re.I)
UA = "pulaski-building-map/1.0 (+https://github.com/brandongrant/pulaski_building_map)"
MAX_NEW_PDFS = 8          # a full first run is ~7 files; keeps a bad day bounded

# Exactly what leaves this pipeline. Narratives are read for tags and dropped;
# no field here can hold a person's name, and the test suite pins that.
PUBLISH_FIELDS = (
    "no", "date", "date_exact", "dt", "cat", "call_type", "call_type_label",
    "offenses", "district", "loc", "loc_src", "tags", "lon", "lat", "gq",
    "report_date", "url", "pdf_page", "parsed",
)

# Offense wording on the statute lines, mapped onto the dispatch taxonomy so the
# reports colour-code with everything else on the site. Checked before the
# generic dispatch categoriser, which is tuned for CAD call types.
OFFENSE_CAT = [
    ("robbery",   r"ROBBERY"),
    ("shots",     r"DISCHARGE OF A FIREARM|TERRORISTIC ACT|POSSESSION OF A (HAND|FIRE)ARM|WEAPON"),
    ("assault",   r"ASSAULT|BATTERY|HOMICIDE|MURDER|MANSLAUGHTER"),
    ("burglary",  r"BURGLARY|BREAKING (OR|AND) ENTERING"),
    ("theft",     r"THEFT|SHOPLIFT|LARCENY|STOLEN"),
    ("fraud",     r"FRAUD|FORGERY|CREDIT CARD|IDENTITY"),
    ("vandalism", r"CRIMINAL MISCHIEF|VANDAL|GRAFFITI"),
    ("drugs",     r"CONTROLLED SUBSTANCE|DRUG PARAPHERNALIA|NARCOTIC"),
    ("sex",       r"\bRAPE\b|SEXUAL|INDECENT"),
    ("domestic",  r"FAMILY OR HOUSEHOLD MEMBER|DOMESTIC"),
]
OFFENSE_CAT_RE = [(k, re.compile(p)) for k, p in OFFENSE_CAT]


def classify(rec):
    """Pick one dispatch category for an incident record."""
    text = " ".join(rec.get("offenses") or [])
    for key, rx in OFFENSE_CAT_RE:
        if rx.search(text):
            return key
    label = f"{rec.get('call_type_label', '')} {rec.get('call_type', '')}".upper()
    if label.strip():
        cat = categorize(label)
        if cat != "other":
            return cat
    # scanned cover page: the tags are all we have to go on
    tags = set(rec.get("tags") or [])
    for tag, cat in (("firearm", "shots"), ("knife", "assault"),
                     ("forced_entry", "burglary"), ("property_taken", "theft")):
        if tag in tags:
            return cat
    return "other"


def fetch(url, expect_pdf=False):
    for i in range(4):
        try:
            r = requests.get(url, timeout=90, headers={"User-Agent": UA})
            r.raise_for_status()
            if expect_pdf and not r.content.startswith(b"%PDF"):
                raise ValueError("not a PDF")
            return r
        except Exception as e:
            print(f"fetch retry {i + 1} for {url}: {e}", file=sys.stderr)
            time.sleep(6 * (i + 1))
    return None


def list_reports(html):
    """-> [(iso_date, url)] newest first, deduped."""
    found = {}
    for m in PDF_RE.finditer(html):
        mo, dy, yr = m.group(1), m.group(2), m.group(3)
        try:
            iso = datetime(int(yr), int(mo), int(dy)).strftime("%Y-%m-%d")
        except ValueError:
            continue
        found[iso] = m.group(0)
    return sorted(found.items(), reverse=True)


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--min-interval-hours", type=float, default=4.0,
                    help="skip the run unless this long since the last check")
    ap.add_argument("--force", action="store_true", help="ignore the interval gate")
    ap.add_argument("--rebuild-only", action="store_true",
                    help="no fetch; re-geocode the archive and rewrite outputs")
    args = ap.parse_args()

    store = Path(args.store) / "reports"
    raw_dir, out_dir = store / "raw", store / "out"
    raw_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    seen_path, state_path = store / "seen.json", store / "state.json"
    seen = load_json(seen_path, {})
    state = load_json(state_path, {})
    now = datetime.now(timezone.utc)

    if not args.rebuild_only:
        last = state.get("last_check")
        if last and not args.force:
            try:
                age = (now - datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ")
                       .replace(tzinfo=timezone.utc)).total_seconds() / 3600
                if age < args.min_interval_hours:
                    print(f"checked {age:.1f} h ago (< {args.min_interval_hours}); skipping")
                    return
            except ValueError:
                pass

        page = fetch(PAGE_URL)
        if page is None:
            print("could not read the daily-reports page; leaving the archive alone")
            return
        listed = list_reports(page.text)
        state["last_check"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        state["listed"] = [d for d, _ in listed]
        print(f"listed report dates: {len(listed)}, already archived: {len(seen)}")

        new_rows = []
        fetched = 0
        for iso, url in listed:
            if iso in seen or fetched >= MAX_NEW_PDFS:
                continue
            r = fetch(url, expect_pdf=True)
            if r is None:
                continue
            fetched += 1
            try:
                parsed = lrpd_reports.parse(io.BytesIO(r.content))
            except Exception as e:
                print(f"parse failed for {iso}: {e}", file=sys.stderr)
                seen[iso] = {"url": url, "error": str(e)[:120],
                             "collected": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
                continue
            for inc in parsed["incidents"]:
                inc["report_date"] = iso
                inc["url"] = url
                inc["collected"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
                new_rows.append(inc)
            seen[iso] = {"url": url, "pages": parsed["pages"],
                         "text_pages": parsed["text_pages"],
                         "incidents": len(parsed["incidents"]),
                         "collected": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
            print(f"  {iso}: {parsed['pages']} pages "
                  f"({parsed['text_pages']} with text) -> "
                  f"{len(parsed['incidents'])} incidents")
            time.sleep(1.5)               # be a polite guest on the city's server

        if new_rows:
            by_month = {}
            for r in new_rows:
                month = (r.get("date") or r["report_date"])[:7]
                by_month.setdefault(month, []).append(r)
            for month, rows in by_month.items():
                with open(raw_dir / f"{month}.jsonl", "a", encoding="utf-8") as f:
                    for r in rows:
                        f.write(json.dumps(r, ensure_ascii=False) + "\n")
        seen_path.write_text(json.dumps(seen, indent=1, sort_keys=True), encoding="utf-8")
        state_path.write_text(json.dumps(state, indent=1), encoding="utf-8")
        print(f"new PDFs: {fetched}, new incidents: {len(new_rows)}")

    # ---------------- rebuild published outputs from the whole archive -------
    geo = Geocoder(Path(args.store) / "dispatch" / "address_index.json.gz")
    records, by_no = [], {}
    for f in sorted(raw_dir.glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except Exception:
                continue
            # a later, fuller parse of the same incident supersedes an earlier one
            prev = by_no.get(r["no"])
            if prev and prev.get("parsed") == "full" and r.get("parsed") != "full":
                continue
            by_no[r["no"]] = r

    placed = 0
    for r in by_no.values():
        lon = lat = None
        gq = "none"
        if r.get("loc"):
            lon, lat, gq = geo.geocode(r["loc"])
        r["lon"], r["lat"], r["gq"] = lon, lat, gq
        r["cat"] = classify(r)
        placed += lon is not None
        records.append(r)

    records.sort(key=lambda r: (r.get("date") or "", r.get("dt") or "", r["no"]),
                 reverse=True)
    pub = [{k: r[k] for k in PUBLISH_FIELDS if k in r} for r in records]
    (out_dir / "incidents.json").write_text(
        json.dumps({"updated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "count": len(pub), "placed": placed,
                    "source": PAGE_URL,
                    "tag_labels": lrpd_reports.TAG_LABELS,
                    "incidents": pub}, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8")

    from collections import Counter
    cats = Counter(r["cat"] for r in records)
    dates = [r.get("date") for r in records if r.get("date")]
    (out_dir / "stats.json").write_text(json.dumps({
        "updated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reports_collected": len(seen),
        "first_report": min(seen, default=None),
        "last_report": max(seen, default=None),
        "incidents": len(records),
        "full": sum(1 for r in records if r.get("parsed") == "full"),
        "partial": sum(1 for r in records if r.get("parsed") != "full"),
        "placed": placed,
        "earliest": min(dates, default=None), "latest": max(dates, default=None),
        "by_category": dict(cats),
    }, indent=1), encoding="utf-8")
    print(f"archive: {len(records)} incidents from {len(seen)} report PDFs, "
          f"{placed} placed on the map")


if __name__ == "__main__":
    main()
