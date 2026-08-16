"""Build the surveillance device map from three kinds of source.

  1. ARDOT's own published camera layer (layers.idrivearkansas.com) - the
     devices the state tells you about, with live stream links.
  2. OpenStreetMap's surveillance tagging, which is where the DeFlock project
     and local mappers record plate readers and gunshot sensors.
  3. Hand-checked field sightings in pipeline/surveillance/sightings.json -
     things photographed on the street that are not in either feed.

Output: web/data/surveillance/{devices.geojson,programs.json,documents.json,meta.json}

Every device carries the programme it belongs to, and every programme carries
the documents that paid for it, so a click on a pin can reach the resolution
number and the account it was paid from.

    python pipeline/build_surveillance.py            # fetch fresh
    python pipeline/build_surveillance.py --offline  # rebuild from cache
"""
import argparse
import json
import math
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common.settings import RAW_DIR, WEB_DATA_DIR  # noqa: E402

SRC = Path(__file__).parent / "surveillance"
OUT = WEB_DATA_DIR / "surveillance"
CACHE = RAW_DIR / "surveillance"

# The window the map covers: Pulaski County plus a margin so devices just over
# the line (Maumelle, Sherwood, Alexander) are not silently cut off.
BBOX = (34.55, -92.90, 35.10, -91.95)          # south, west, north, east

CAMERAS_URL = "https://layers.idrivearkansas.com/cameras.geojson"
# LRPD's own list of its plate readers, released under FOIA and published by
# the Arkansas Times. Addresses only, so they are geocoded against PAgis.
FOIA_DOC_ID = "pdfoia-2025-4004-7dbb64"
LOCATOR = ("https://www.pagis.org/arcgis/rest/services/LOCATORS/"
           "CompositeLoc_PAgis_2025/GeocodeServer/findAddressCandidates")
GEOCACHE = SRC / "geocache.json"
STATUS_RE = re.compile(
    r"^(.*?)\s+(In Service|Optimizing|Permitting|Not In Service|Pending|Removed|Offline)\s*$",
    re.I)
# A camera in the FOIA list and a camera mapped by a volunteer within this many
# metres are treated as the same physical pole.
SAME_POLE_M = 70
OVERPASS = "https://overpass.kumi.systems/api/interpreter"
BOX = f"{BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]}"
OVERPASS_QUERY = f"""[out:json][timeout:180];
(
  node["man_made"="surveillance"]({BOX});
  way["man_made"="surveillance"]({BOX});
  node["highway"="speed_camera"]({BOX});
  node["surveillance:type"]({BOX});
  way["surveillance:type"]({BOX});
  node["enforcement"]({BOX});
);
out center tags;"""

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# OSM operator/manufacturer strings -> our programme ids.
def alpr_program(tags):
    maker = (tags.get("manufacturer") or "").lower()
    operator = (tags.get("operator") or "").lower()
    if "liveview" in maker:               # trailer-mounted camera towers
        return "lvt-tower"
    if "flock" in maker or "flock" in operator:
        if "little rock police" in operator and "north" not in operator:
            return "flock-lrpd"
        # Most mapped readers carry no operator tag. Saying "some other agency
        # owns it" would be a claim the data does not support, so they get
        # their own bucket.
        return "flock-other" if operator else "flock-unattributed"
    return "other-alpr"


def get(url, cache_name, offline, post=None):
    """Fetch a source, keeping a copy so --offline can rebuild without network."""
    path = CACHE / cache_name
    if offline:
        if path.exists():
            print(f"  using cached {cache_name}")
            return path.read_bytes()
        raise SystemExit(f"--offline but no cache at {path}")
    data = post.encode() if post else None
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=180) as r:
        raw = r.read()
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    print(f"  fetched {cache_name} ({len(raw) / 1000:.0f} KB)")
    return raw


def haversine(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def in_bbox(lat, lon):
    return BBOX[0] <= lat <= BBOX[2] and BBOX[1] <= lon <= BBOX[3]


def clean(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()


def load_ardot(offline):
    raw = get(CAMERAS_URL, "cameras.geojson", offline)
    feats = json.loads(raw)["features"]
    out = []
    for f in feats:
        lon, lat = f["geometry"]["coordinates"]
        if not in_bbox(lat, lon):
            continue
        p = f["properties"]
        props = {
            "id": f"ardot-{p['id']}",
            "fam": "traffic",
            "prog": "ardot-cctv",
            "lbl": clean(p.get("name")) or "ARDOT traffic camera",
            "src": "ardot",
            "public": 1,
            "route": clean(p.get("route")),
            "model": clean(p.get("camera")),
            "url": p.get("hls_stream_protected") or "",
        }
        out.append({"type": "Feature", "properties": props,
                    "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]}})
    print(f"  ARDOT cameras in window: {len(out)} of {len(feats)} statewide")
    return out


def load_osm(offline):
    raw = get(OVERPASS, "osm_surveillance.json", offline, post=OVERPASS_QUERY)
    els = json.loads(raw)["elements"]
    out = []
    for e in els:
        lat = e.get("lat") or (e.get("center") or {}).get("lat")
        lon = e.get("lon") or (e.get("center") or {}).get("lon")
        if lat is None or lon is None or not in_bbox(lat, lon):
            continue
        t = e.get("tags", {})
        stype = (t.get("surveillance:type") or "").lower()
        if stype == "alpr":
            fam, prog = "alpr", alpr_program(t)
        elif stype in ("gunshot_detector", "gunshot"):
            fam, prog = "gunshot", "shotspotter"
        elif t.get("highway") == "speed_camera" or t.get("enforcement"):
            fam, prog = "enforcement", "photo-enforcement"
        else:
            fam, prog = "camera", "other-camera"
        maker = clean(t.get("manufacturer"))
        operator = clean(t.get("operator"))
        props = {
            "id": f"osm-{e['type'][0]}{e['id']}",
            "fam": fam,
            "prog": prog,
            "lbl": maker or operator or ("Plate reader" if fam == "alpr" else "Camera"),
            "src": "osm",
            "public": 0,
            "op": operator,
            "make": maker,
            "url": f"https://www.openstreetmap.org/{e['type']}/{e['id']}",
        }
        direction = t.get("direction") or t.get("camera:direction")
        try:
            props["dir"] = round(float(direction) % 360, 1)
        except (TypeError, ValueError):
            pass
        if t.get("surveillance:zone"):
            props["zone"] = clean(t["surveillance:zone"])
        out.append({"type": "Feature", "properties": props,
                    "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]}})
    fams = {}
    for f in out:
        fams[f["properties"]["fam"]] = fams.get(f["properties"]["fam"], 0) + 1
    print(f"  OpenStreetMap surveillance nodes in window: {len(out)} {fams}")
    return out


# Open Location Code, base 20. One entry in LRPD's list is a Google Plus Code
# rather than a street address, so it is decoded rather than geocoded.
OLC_ALPHABET = "23456789CFGHJMPQRVWX"
OLC_RES = [20.0, 1.0, 0.05, 0.0025, 0.000125]
LITTLE_ROCK = (34.7465, -92.2896)
RE_PLUSCODE = re.compile(r"\b([23456789CFGHJMPQRVWX]{4,6}\+[23456789CFGHJMPQRVWX]{2,3})\b")


def olc_encode(lat, lon):
    lat, lon = lat + 90.0, lon + 180.0
    out = ""
    for r in OLC_RES:
        out += OLC_ALPHABET[min(19, int(lat // r))] + OLC_ALPHABET[min(19, int(lon // r))]
        lat, lon = lat % r, lon % r
    return out


def olc_decode(code):
    code = code.replace("+", "")
    lat, lon = -90.0, -180.0
    for i in range(0, len(code) - 1, 2):
        r = OLC_RES[i // 2]
        lat += OLC_ALPHABET.index(code[i]) * r
        lon += OLC_ALPHABET.index(code[i + 1]) * r
    half = OLC_RES[(len(code) // 2) - 1] / 2
    return lat + half, lon + half


def plus_code(address):
    """Decode a short plus code against Little Rock, or return None."""
    m = RE_PLUSCODE.search(address.upper())
    if not m or "+" not in m.group(1):
        return None
    short = m.group(1)
    if len(short.split("+")[0]) >= 8:                  # already a full code
        lat, lon = olc_decode(short)
    else:
        prefix = olc_encode(*LITTLE_ROCK)[:8 - len(short.split("+")[0])]
        lat, lon = olc_decode(prefix + short)
    return [round(lon, 6), round(lat, 6), 90.0]


def geocode(address, cache, offline):
    """PAgis composite locator; results are cached in the repo so rebuilds
    are reproducible and do not re-hit the service."""
    if address in cache and cache[address]:
        return cache[address]
    decoded = plus_code(address)
    if decoded:
        cache[address] = decoded
        return decoded
    if address in cache:
        return cache[address]
    if offline:
        return None
    query = urllib.parse.urlencode({"SingleLine": address, "f": "json",
                                    "outSR": 4326, "maxLocations": 1})
    try:
        with urllib.request.urlopen(f"{LOCATOR}?{query}", timeout=45) as r:
            candidates = json.load(r).get("candidates") or []
    except Exception as exc:
        print(f"    geocode failed for {address[:50]}: {exc}")
        return None
    if not candidates:
        cache[address] = None
        return None
    best = candidates[0]
    cache[address] = [round(best["location"]["x"], 6),
                      round(best["location"]["y"], 6), best.get("score", 0)]
    return cache[address]


def load_lrpd_foia(offline):
    """The 116 LRPD plate readers, from the department's own FOIA response."""
    text_path = SRC / "doc_text" / f"{FOIA_DOC_ID}.txt"
    if not text_path.exists():
        print("  LRPD FOIA list not filed yet - skipping "
              "(run surveillance_docs.py add on the FOIA PDF)")
        return []
    cache = json.loads(GEOCACHE.read_text(encoding="utf-8")) if GEOCACHE.exists() else {}
    out, unmatched = [], 0
    for line in text_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        m = STATUS_RE.match(line)
        if not m or line.lower().startswith("address"):
            continue
        address, status = m.group(1).strip().rstrip(","), m.group(2).title()
        point = geocode(address, cache, offline)
        if not point:
            unmatched += 1
            continue
        lon, lat, score = point
        short = re.sub(r",\s*(Little Rock|Mabelvale|North Little Rock),?\s*AR.*$", "",
                       address, flags=re.I)
        out.append({"type": "Feature",
                    "properties": {
                        "id": f"foia-{len(out):03d}",
                        "fam": "alpr",
                        "prog": "flock-lrpd",
                        "lbl": short,
                        "src": "foia",
                        "public": 1,
                        "op": "Little Rock Police Department",
                        "make": "Flock Safety",
                        "status": status,
                        "addr": address,
                        "geo_score": round(score, 1),
                        "url": ("https://arktimes.com/wp-content/uploads/2026/01/"
                                "PDFOIA-2025-4004.pdf"),
                    },
                    "geometry": {"type": "Point", "coordinates": [lon, lat]}})
    GEOCACHE.write_text(json.dumps(cache, indent=0, sort_keys=True), encoding="utf-8")
    print(f"  LRPD FOIA plate readers: {len(out)} geocoded"
          f"{f', {unmatched} unmatched' if unmatched else ''}")
    return out


def merge_duplicate_poles(foia, osm):
    """Drop volunteer-mapped readers that are the same pole as a FOIA record.

    Both sources are kept honestly: the authoritative record wins the pin and
    inherits the direction the volunteer recorded, and we count how many of the
    department's cameras the open map had already found.
    """
    kept, merged = [], 0
    for f in osm:
        if f["properties"]["fam"] != "alpr":
            kept.append(f)
            continue
        lon, lat = f["geometry"]["coordinates"]
        match = None
        for g in foia:
            glon, glat = g["geometry"]["coordinates"]
            if haversine(lat, lon, glat, glon) <= SAME_POLE_M:
                match = g
                break
        if match:
            merged += 1
            match["properties"]["osm_seen"] = 1
            if "dir" in f["properties"] and "dir" not in match["properties"]:
                match["properties"]["dir"] = f["properties"]["dir"]
        else:
            kept.append(f)
    print(f"  merged {merged} volunteer-mapped readers onto their FOIA record")
    return kept, merged


def streetview(lat, lon, heading, pitch):
    return ("https://www.google.com/maps/@?api=1&map_action=pano"
            f"&viewpoint={lat},{lon}&heading={heading}&pitch={pitch}")


def load_sightings(ardot, osm):
    """Field observations, each scored against the published feeds."""
    items = json.loads((SRC / "sightings.json").read_text(encoding="utf-8"))
    alprs = [f for f in osm if f["properties"]["fam"] == "alpr"]
    out = []
    for s in items:
        lat, lon = s["lat"], s["lon"]

        def nearest(features):
            best = None
            for f in features:
                flon, flat = f["geometry"]["coordinates"]
                d = haversine(lat, lon, flat, flon)
                if best is None or d < best[0]:
                    best = (d, f["properties"])
            return best

        near_cam = nearest(ardot)
        near_alpr = nearest(alprs)
        props = {
            "id": f"sight-{s['id']}",
            "fam": "sighting",
            "prog": s.get("program", "unidentified"),
            "lbl": s["title"],
            "src": "sighting",
            "public": 0,
            "where": s.get("where", ""),
            "conf": s.get("confidence", "uncertain"),
            "why": s.get("reasoning", ""),
            "confirm": s.get("confirm", ""),
            "note": s.get("note", ""),
            "timeline": s.get("timeline", []),
            "url": streetview(lat, lon, s.get("heading", 0), s.get("pitch", 0)),
        }
        if near_cam:
            props["near_cam_m"] = round(near_cam[0])
            props["near_cam"] = near_cam[1]["lbl"]
        if near_alpr:
            props["near_alpr_m"] = round(near_alpr[0])
        out.append({"type": "Feature", "properties": props,
                    "geometry": {"type": "Point", "coordinates": [lon, lat]}})
    print(f"  field sightings: {len(out)}")
    return out


def spend_timeline(documents):
    """One headline figure per document, in date order - never a running total.

    Renewals and amendments overlap, so adding them up would overstate what was
    spent. The page shows them as a sequence of decisions instead.
    """
    rows = []
    for d in documents:
        if not d.get("amounts") or not d.get("date"):
            continue
        top = d["amounts"][0]
        rows.append({
            "date": d["date"],
            "amount": top["value"],
            "literal": top["literal"],
            "programs": d.get("programs", []),
            "doc": d["id"],
            "title": d.get("title", ""),
        })
    return sorted(rows, key=lambda r: r["date"])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--offline", action="store_true",
                    help="rebuild from cached downloads, no network")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    print("sources:")
    ardot = load_ardot(args.offline)
    osm = load_osm(args.offline)
    foia = load_lrpd_foia(args.offline)
    osm, corroborated = merge_duplicate_poles(foia, osm)
    sightings = load_sightings(ardot, osm + foia)
    features = ardot + foia + osm + sightings

    programs = json.loads((SRC / "programs.json").read_text(encoding="utf-8"))
    documents = json.loads((SRC / "documents.json").read_text(encoding="utf-8"))

    # Attach each programme's documents so the app can go device -> paper.
    for pid, prog in programs.items():
        prog["documents"] = [d["id"] for d in documents if pid in d.get("programs", [])]

    counts = {}
    by_program = {}
    for f in features:
        p = f["properties"]
        counts[p["fam"]] = counts.get(p["fam"], 0) + 1
        by_program[p["prog"]] = by_program.get(p["prog"], 0) + 1

    (OUT / "devices.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": features}),
        encoding="utf-8")
    (OUT / "programs.json").write_text(
        json.dumps(programs, indent=1, ensure_ascii=False), encoding="utf-8")
    (OUT / "documents.json").write_text(
        json.dumps(documents, indent=1, ensure_ascii=False), encoding="utf-8")

    meta = {
        "generated": time.strftime("%Y-%m-%d"),
        "bbox": list(BBOX),
        "counts": counts,
        "by_program": by_program,
        "devices": len(features),
        "documents": len(documents),
        "lrpd_foia_readers": len(foia),
        "lrpd_foia_also_mapped_by_volunteers": corroborated,
        "spend": spend_timeline(documents),
        "sources": [
            {"name": "ARDOT published traffic cameras", "url": CAMERAS_URL},
            {"name": "OpenStreetMap surveillance tagging (DeFlock and local mappers)",
             "url": "https://www.openstreetmap.org"},
            {"name": "Field sightings verified in Google Street View", "url": ""},
        ],
    }
    (OUT / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")

    size = (OUT / "devices.geojson").stat().st_size / 1000
    print(f"\nwrote {len(features)} devices ({size:.0f} KB), "
          f"{len(programs)} programmes, {len(documents)} documents")
    print(f"  by family:  {counts}")
    print(f"  by program: {by_program}")


if __name__ == "__main__":
    main()
