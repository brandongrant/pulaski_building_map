"""Load the canonical Phase 1 property model into Postgres/PostGIS (Neon).

Reads ONLY artifacts this repo already produces — no source re-downloads:

  data/processed/parcel_owners.pkl     property spine (fresh PAgis parcel roll)
  data/processed/cama_parcel_attrs.pkl assessor attributes (year built, ...)
  data/processed/buildings_final.pkl   footprint geometry + per-building attrs
  web/data/buildings.pmtiles           z13 features carry pid + fresh values
                                       (the local pkl predates the Phase-0B
                                       rebuild, so pid comes from our tiles)
  web/data/permits/permits.geojson     LR permit events
  <data branch>/sr311/out/requests.geojson   311 events (fetched over HTTPS)
  <data branch>/deeds/out/recent_activity.geojson  recorded-document events

Dispatch calls are deliberately NOT loaded: the project's privacy policy
serves them as exact points for 24 h and aggregates afterwards, so a
permanent per-property dispatch history would violate it.

Identity: deterministic uuid5 ids (property = jurisdiction+parcel,
building = source building id, entity = normalized name, event =
source+key+type) so re-running upserts in place and profile URLs are stable.

Usage:
  python pipeline/load_db.py --dry-run          # parse inputs, no database
  python pipeline/load_db.py                    # full load / re-load
  python pipeline/load_db.py --limit 500        # smoke-load a subset
Connection: PULASKI_DATABASE_URL or DATABASE_URL env var, or --env-file
pointing at a KEY=VALUE file (e.g. .env — gitignored, never commit it).
"""
import argparse
import gzip
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from common.settings import PROCESSED_DIR, REPO_ROOT, WEB_DATA_DIR

JURISDICTION = "us-ar-pulaski"
DATA_BRANCH = "https://raw.githubusercontent.com/brandongrant/pulaski_building_map/data"
NS = uuid.uuid5(uuid.NAMESPACE_URL, "https://github.com/brandongrant/pulaski_building_map")
FULL_PROPS_Z = 13   # zoom whose tiles carry pid/addr for every building

SOURCES = {
    # slug -> metadata (mirrors jurisdictions/ar/pulaski.yml)
    "pagis-parcel-roll": dict(
        name="PAgis parcel roll (owner/situs/values)", entity_grain="parcel",
        source_url="https://www.pagis.org/arcgis/rest/services/MAPS/BaseMap/MapServer/68",
        source_owner="Pulaski Area GIS / Pulaski County Assessor",
        refresh_cadence="on demand", sensitivity_class="public_property"),
    "pulaski-cama": dict(
        name="Pulaski County Assessor CAMA export", entity_grain="parcel",
        source_url="https://pulaskicountyassessor.net/services/raw-data-export/",
        source_owner="Pulaski County Assessor",
        refresh_cadence="monthly-ish", sensitivity_class="public_property"),
    "pagis-buildings": dict(
        name="PAgis building footprints", entity_grain="building",
        source_url="https://www.pagis.org/arcgis/rest/services/MAPS/BaseMap/MapServer/21",
        source_owner="Pulaski Area GIS",
        refresh_cadence="on demand", sensitivity_class="public_property"),
    "lr-permits": dict(
        name="City of Little Rock building permits", entity_grain="permit",
        source_url="https://littlerock.gov/government/mayors-office/initiatives/city-of-lr-data/",
        source_owner="City of Little Rock Planning & Development",
        refresh_cadence="monthly", sensitivity_class="public_property_event"),
    "lr-311": dict(
        name="City of Little Rock 311 service requests", entity_grain="service_request",
        source_url="https://littlerock-cwiprod.motorolasolutions.com/cwi/search",
        source_owner="City of Little Rock",
        refresh_cadence="15 min (collector)", sensitivity_class="public_property_event"),
    "pulaski-deeds-index": dict(
        name="Pulaski County recorded-document index", entity_grain="document",
        source_url="https://pulaskideeds.com/search/index.php?Accept=Accept",
        source_owner="Pulaski County Circuit/County Clerk",
        refresh_cadence="15 min (collector)", sensitivity_class="public_property_event"),
}

PM_LABELS = {"new": "New construction", "add": "Addition", "rem": "Remodel/repair",
             "demo": "Demolition", "usv": "Unsafe/vacant", "roof": "Roofing",
             "ele": "Electrical", "mec": "Mechanical", "plu": "Plumbing",
             "sign": "Sign/banner", "oth": "Other"}
PM_STATUS = {"O": "open", "C": "closed", "W": "stop_work"}
DEED_TYPE_MAP = {"deed": ("ownership_transfer", None),
                 "mtg": ("mortgage_recorded", None),
                 "rel": ("mortgage_released", None),
                 "asgn": ("mortgage_recorded", "assignment"),
                 "lien": ("lien_recorded", None),
                 "fcl": ("foreclosure_notice", None),
                 "plat": ("plat_recorded", None),
                 "ease": ("easement_recorded", None),
                 "oth": ("document_recorded", None)}

ORG_RE = re.compile(r"\b(LLC|L L C|INC|CORP|CORPORATION|LP|LLP|LTD|COMPANY|PARTNERSHIP|"
                    r"CHURCH|BANK|PROPERTIES|INVESTMENTS|HOLDINGS|ASSN|ASSOCIATION|HOA|"
                    r"APARTMENTS|DEVELOPMENT|ENTERPRISES|MINISTRIES|REALTY|GROUP)\b")
TRUST_RE = re.compile(r"\b(TRUST|TRUSTEE|TRS?|REVOCABLE|LIVING TR)\b")
GOV_RE = re.compile(r"\b(CITY OF|COUNTY|STATE OF|UNITED STATES|USA|SCHOOL DIST|"
                    r"HOUSING AUTH|ARKANSAS|PULASKI CO|GOVERNMENT|AUTHORITY)\b")


def norm_key(s):
    return re.sub(r"[^A-Z0-9 ]+", " ", str(s or "").upper()).strip()


def squash_ws(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()


def norm_parcel(s):
    return re.sub(r"[^0-9A-Za-z]", "", str(s or "")).upper()


def entity_type(name):
    n = " " + norm_key(name) + " "
    if GOV_RE.search(n):
        return "government"
    if TRUST_RE.search(n):
        return "trust"
    if ORG_RE.search(n):
        return "organization"
    return "person"


def day_to_date(d):
    s = str(int(d))
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def uid(*parts):
    return str(uuid.uuid5(NS, "|".join(str(p) for p in parts)))


def payload_hash(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def git_commit():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
                              capture_output=True, text=True, timeout=10).stdout.strip()[:40]
    except Exception:
        return None


# ------------------------------------------------------------------ inputs
def tile_xy(lon, lat, z):
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.log(math.tan(math.radians(lat)) +
                          1 / math.cos(math.radians(lat))) / math.pi) / 2 * n)
    return x, y


def read_tile_pids():
    """Decode every z13 tile in our own pmtiles -> {building_id: props}."""
    from pmtiles.reader import Reader, MmapSource
    import mapbox_vector_tile
    out = {}
    with open(WEB_DATA_DIR / "buildings.pmtiles", "rb") as f:
        r = Reader(MmapSource(f))
        h = r.header()
        lon0, lat0 = h["min_lon_e7"] / 1e7, h["min_lat_e7"] / 1e7
        lon1, lat1 = h["max_lon_e7"] / 1e7, h["max_lat_e7"] / 1e7
        x0, y1 = tile_xy(lon0, lat0, FULL_PROPS_Z)   # y grows southward
        x1, y0 = tile_xy(lon1, lat1, FULL_PROPS_Z)
        for x in range(min(x0, x1), max(x0, x1) + 1):
            for y in range(min(y0, y1), max(y0, y1) + 1):
                data = r.get(FULL_PROPS_Z, x, y)
                if not data:
                    continue
                try:
                    data = gzip.decompress(data)
                except Exception:
                    pass
                layers = mapbox_vector_tile.decode(data)
                for feat in layers.get("buildings", {}).get("features", []):
                    fid = feat.get("id")
                    if fid is None or fid in out:
                        continue
                    out[fid] = feat.get("properties", {})
    return out


def fetch_json(url):
    import requests
    r = requests.get(url, timeout=120)
    if not r.ok:
        return None
    return r.json()


def load_inputs(limit=None):
    t0 = time.time()
    po = pd.read_pickle(PROCESSED_DIR / "parcel_owners.pkl")
    ca = pd.read_pickle(PROCESSED_DIR / "cama_parcel_attrs.pkl")
    bf = pd.read_pickle(PROCESSED_DIR / "buildings_final.pkl")
    if limit:
        po = po.head(limit)
    ca["pnorm"] = ca.ParcelNumber.map(norm_parcel)
    cama = ca.drop_duplicates("pnorm").set_index("pnorm")[
        ["year_built", "stories", "sqft", "category", "n_bldgs"]].to_dict("index")
    tp = read_tile_pids()
    permits = json.load(open(WEB_DATA_DIR / "permits" / "permits.geojson", encoding="utf-8"))
    sr311 = fetch_json(f"{DATA_BRANCH}/sr311/out/requests.geojson")
    deeds = fetch_json(f"{DATA_BRANCH}/deeds/out/recent_activity.geojson")
    print(f"inputs: {len(po)} parcels, {len(cama)} CAMA rows, {len(bf)} buildings, "
          f"{len(tp)} tile pid rows, {len(permits['features'])} permits, "
          f"{len(sr311['features']) if sr311 else 0} 311 requests, "
          f"{len(deeds['features']) if deeds else 0} deed docs  ({time.time()-t0:.0f}s)")
    return po, cama, bf, tp, permits, sr311, deeds


# ------------------------------------------------------------------ assembly
def build_rows(po, cama, bf, tp, permits, sr311, deeds, as_of):
    now = datetime.now(timezone.utc).isoformat()
    rows = {k: [] for k in ("source_record", "property", "snapshot", "entity",
                            "interest", "building", "event", "match")}
    run_ids = {slug: str(uuid.uuid4()) for slug in SOURCES}

    # ---- properties + snapshots + owner entities
    addr_city_to_pid = {}      # norm addr|city -> property_id
    addr_to_pids = {}          # norm addr -> set(property_id)
    parcel_to_pid = {}
    seen_entities = set()
    for r in po.itertuples(index=False):
        pnorm = norm_parcel(r.parcelid)
        if not pnorm:
            continue
        pid = uid("property", JURISDICTION, pnorm)
        parcel_to_pid[pnorm] = pid
        a, c = norm_key(squash_ws(r.addr)), norm_key(r.city)
        if a:
            addr_city_to_pid.setdefault(a + "|" + c, pid)
            addr_to_pids.setdefault(a, set()).add(pid)
        payload = {"parcelid": r.parcelid, "owner": r.owner, "addr": r.addr,
                   "city": r.city, "subdiv": r.subdiv, "lot": r.lot, "block": r.block,
                   "legal": r.legal, "total_value": r.total_value,
                   "imp_value": r.imp_value, "assess_value": r.assess_value,
                   "parcel_type": r.parcel_type}
        ph = payload_hash(payload)
        src_id = uid("srcrec", "pagis-parcel-roll", pnorm, ph)
        rows["source_record"].append((src_id, "pagis-parcel-roll", r.parcelid, None,
                                      now, as_of.isoformat(), ph, json.dumps(payload)))
        rows["property"].append((pid, JURISDICTION, r.parcelid, pnorm,
                                 squash_ws(r.addr) or None, a or None, r.city or None, "AR",
                                 r.lon if pd.notna(r.lon) else None,
                                 r.lat if pd.notna(r.lat) else None))
        cm = cama.get(pnorm, {})
        yb = cm.get("year_built")
        st = cm.get("stories")
        sq = cm.get("sqft")
        rows["snapshot"].append((
            uid("snapshot", pnorm, as_of.isoformat()), pid, src_id, as_of.isoformat(),
            r.owner or None, norm_key(r.owner) or None,
            int(yb) if yb and not pd.isna(yb) else None,
            float(st) if st is not None and not pd.isna(st) else None,
            int(sq) if sq and not pd.isna(sq) else None,
            None if pd.isna(r.total_value) else
            float(r.total_value) - (0.0 if pd.isna(r.imp_value) else float(r.imp_value)),
            None if pd.isna(r.imp_value) else float(r.imp_value),
            None if pd.isna(r.total_value) else float(r.total_value),
            None if pd.isna(r.assess_value) else float(r.assess_value),
            str(cm.get("category") or r.parcel_type or "") or None,
            r.legal or None, r.subdiv or None, str(r.lot or "") or None,
            str(r.block or "") or None,
            json.dumps({"parcel_type": r.parcel_type, "owner_city": r.owner_city,
                        "owner_st": r.owner_st, "cama_n_bldgs": cm.get("n_bldgs")})))
        owner = squash_ws(r.owner)
        if owner:
            en = norm_key(owner)
            eid = uid("entity", en)
            if en not in seen_entities:
                seen_entities.add(en)
                rows["entity"].append((eid, entity_type(owner), owner, en))
            rows["interest"].append((uid("interest", pnorm, en, "owner"), pid, eid,
                                     "owner", src_id, 0.9))

    # ---- buildings (tile pid join first, address fallback)
    n_pid = n_addr = n_none = 0
    for r in bf.itertuples(index=False):
        props = tp.get(r.id, {})
        pn = norm_parcel(props.get("pid", ""))
        prop_id = parcel_to_pid.get(pn) if pn else None
        method, conf = None, None
        if prop_id:
            method, conf = "parcel_spatial_join", 0.97
            n_pid += 1
        else:
            a, c = norm_key(squash_ws(r.addr)), norm_key(r.city)
            prop_id = addr_city_to_pid.get(a + "|" + c) if a else None
            if prop_id:
                method, conf = "address", 0.8
                n_addr += 1
            else:
                n_none += 1
        try:
            pt = r.geometry.representative_point()
            lon, lat = float(pt.x), float(pt.y)
        except Exception:
            lon = lat = None
        payload = {"building_id": int(r.id), "pid": props.get("pid"),
                   "yr": props.get("yr", r.yr), "cat": props.get("cat", r.cat)}
        ph = payload_hash(payload)
        src_id = uid("srcrec", "pagis-buildings", r.id, ph)
        rows["source_record"].append((src_id, "pagis-buildings", str(r.id), None,
                                      now, as_of.isoformat(), ph, json.dumps(payload)))
        rows["building"].append((
            uid("building", JURISDICTION, r.id), JURISDICTION, str(r.id), prop_id,
            lon, lat,
            float(r.fpa) if r.fpa and not pd.isna(r.fpa) else None,
            bool(r.main),
            int(props.get("yr", r.yr) or 0) or None,
            str(props.get("cat", r.cat)),
            float(props.get("st", r.st) or 0) or None,
            int(props.get("sqft", r.sqft) or 0) or None,
            float(props.get("val", r.val) or 0) or None,
            method, conf, src_id))

    # ---- event helpers
    def match_event(eid, a_norm, city_norm, base_method, base_conf, evidence):
        key = a_norm + "|" + (city_norm or "LITTLE ROCK")
        prop = addr_city_to_pid.get(key)
        method, conf = base_method, base_conf
        if not prop:
            cands = addr_to_pids.get(a_norm)
            if cands and len(cands) == 1:
                prop = next(iter(cands))
                method, conf = base_method + "_countywide", base_conf - 0.05
            elif cands:
                lr = addr_city_to_pid.get(a_norm + "|LITTLE ROCK")
                if lr:
                    prop, method, conf = lr, base_method + "_ambiguous", 0.6
        if prop:
            rows["match"].append((eid, prop, method, conf, True, json.dumps(evidence)))
        return prop is not None

    matched = {"permits": 0, "sr311": 0, "deeds": 0}

    for f in permits["features"]:
        p = f["properties"]
        n = str(p.get("n", "")).strip()
        if not n or not p.get("d"):
            continue
        ph = payload_hash(p)
        src_id = uid("srcrec", "lr-permits", n, ph)
        rows["source_record"].append((src_id, "lr-permits", n, None, now, None, ph,
                                      json.dumps(p)))
        cat = p.get("t", "oth")
        etype, esub = ("demolition_permit", None) if cat == "demo" else \
                      ("unsafe_vacant_status", None) if cat == "usv" else \
                      ("permit_issued", cat)
        eid = uid("event", "lr-permits", n, etype)
        lon, lat = (f.get("geometry") or {}).get("coordinates", (None, None))[:2]
        rows["event"].append((eid, JURISDICTION, etype, esub,
                              day_to_date(p["d"]).isoformat(), now,
                              f"{PM_LABELS.get(cat, 'Permit')} permit",
                              squash_ws(p.get("ds")) or None,
                              PM_STATUS.get(p.get("s"), p.get("s")),
                              float(p["v"]) if p.get("v") else None,
                              src_id, n, lon, lat,
                              json.dumps({"category": cat, "sqft": p.get("sf")})))
        if p.get("a") and match_event(eid, norm_key(p["a"]), "LITTLE ROCK",
                                      "address", 0.85, {"addr": p["a"]}):
            matched["permits"] += 1

    for f in (sr311 or {"features": []})["features"]:
        p = f["properties"]
        n = str(p.get("n", "")).strip()
        if not n:
            continue
        ph = payload_hash(p)
        src_id = uid("srcrec", "lr-311", n, ph)
        rows["source_record"].append((src_id, "lr-311", n, None, now, None, ph,
                                      json.dumps(p)))
        eid = uid("event", "lr-311", n, "service_request")
        lon, lat = (f.get("geometry") or {}).get("coordinates", (None, None))[:2]
        event_day = p.get("o") or p.get("u")
        rows["event"].append((eid, JURISDICTION, "service_request", p.get("t"),
                              day_to_date(event_day).isoformat(), now,
                              squash_ws(p.get("ty")) or "311 request",
                              None, p.get("sd"), None, src_id, n, lon, lat,
                              json.dumps({"category": p.get("t"), "channel": p.get("ch"),
                                          "opened": p.get("o"), "closed": p.get("cl"),
                                          "updated": p.get("u")})))
        if p.get("a") and match_event(eid, norm_key(p["a"]), norm_key(p.get("c")) or "LITTLE ROCK",
                                      "address", 0.85, {"addr": p["a"], "gq": p.get("gq")}):
            matched["sr311"] += 1

    for f in (deeds or {"features": []})["features"]:
        p = f["properties"]
        n = str(p.get("n", "")).strip()
        if not n or not p.get("d"):
            continue
        ph = payload_hash(p)
        src_id = uid("srcrec", "pulaski-deeds-index", n, ph)
        rows["source_record"].append((src_id, "pulaski-deeds-index", n,
                                      "https://pulaskideeds.com/search/index.php?Accept=Accept",
                                      now, None, ph, json.dumps(p)))
        etype, esub = DEED_TYPE_MAP.get(p.get("c"), ("document_recorded", None))
        eid = uid("event", "pulaski-deeds-index", n, etype)
        lon, lat = (f.get("geometry") or {}).get("coordinates", (None, None))[:2]
        g1, g2 = squash_ws(p.get("g1")), squash_ws(p.get("g2"))
        conf = {"exact": 0.95, "base": 0.85, "lb": 0.75}.get(p.get("mq"), 0.7)
        rows["event"].append((eid, JURISDICTION, etype, esub or p.get("t"),
                              day_to_date(p["d"]).isoformat(), now,
                              squash_ws(p.get("dt")) or "Recorded document",
                              f"{g1} → {g2}" if g1 or g2 else None,
                              None, None, src_id, n, lon, lat,
                              json.dumps({"grantor": g1, "grantee": g2,
                                          "inst_type": p.get("t"),
                                          "match_quality": p.get("mq")})))
        if p.get("a") and match_event(eid, norm_key(p["a"]), None,
                                      "legal_index_address", conf,
                                      {"addr": p["a"], "mq": p.get("mq")}):
            matched["deeds"] += 1

    print(f"assembled: {len(rows['property'])} properties, {len(rows['building'])} buildings "
          f"(pid {n_pid} / addr {n_addr} / unmatched {n_none}), "
          f"{len(rows['entity'])} entities, {len(rows['event'])} events "
          f"(matched: {matched})")
    return rows, run_ids


# ------------------------------------------------------------------ database
def connect(env_file=None):
    if env_file:
        for line in Path(env_file).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    url = os.environ.get("PULASKI_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("set PULASKI_DATABASE_URL (or DATABASE_URL), or pass --env-file .env")
    import psycopg
    return psycopg.connect(url, autocommit=False)


def apply_migrations(conn):
    mig_dir = REPO_ROOT / "db" / "migrations"
    with conn.cursor() as cur:
        cur.execute("""create table if not exists schema_migrations
                       (filename text primary key, applied_at timestamptz default now())""")
        cur.execute("select filename from schema_migrations")
        done = {r[0] for r in cur.fetchall()}
        for f in sorted(mig_dir.glob("*.sql")):
            if f.name in done:
                continue
            print("applying migration", f.name)
            cur.execute(f.read_text(encoding="utf-8"))
            cur.execute("insert into schema_migrations (filename) values (%s)", (f.name,))
    conn.commit()


def copy_rows(cur, table, cols, data):
    with cur.copy(f"copy {table} ({', '.join(cols)}) from stdin") as cp:
        for row in data:
            cp.write_row(row)


def load(conn, rows, run_ids, as_of):
    commit = git_commit()
    now = datetime.now(timezone.utc)
    with conn.cursor() as cur:
        # sources + runs
        for slug, m in SOURCES.items():
            cur.execute("""
                insert into data_source (source_id, slug, name, jurisdiction_id, source_url,
                                         source_owner, entity_grain, refresh_cadence, sensitivity_class)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                on conflict (slug) do update set name=excluded.name, source_url=excluded.source_url
            """, (uid("source", slug), slug, m["name"], JURISDICTION, m["source_url"],
                  m["source_owner"], m["entity_grain"], m["refresh_cadence"],
                  m["sensitivity_class"]))
        for slug, rid in run_ids.items():
            cur.execute("""insert into ingest_run (ingest_run_id, source_id, code_commit,
                           started_at, status) values (%s,%s,%s,%s,'running')""",
                        (rid, uid("source", slug), commit, now))
        conn.commit()

        # ---- source_record via staging
        cur.execute("""create temp table stg_src (source_record_id uuid, slug text,
            source_key text, url text, observed_at timestamptz, effective_at date,
            payload_hash text, payload jsonb) on commit drop""")
        copy_rows(cur, "stg_src",
                  ["source_record_id", "slug", "source_key", "url", "observed_at",
                   "effective_at", "payload_hash", "payload"], rows["source_record"])
        cur.execute("create temp table stg_runs (slug text, ingest_run_id uuid) on commit drop")
        copy_rows(cur, "stg_runs", ["slug", "ingest_run_id"], list(run_ids.items()))
        cur.execute("""insert into source_record (source_record_id, source_id, ingest_run_id,
                source_key, source_record_url, observed_at, effective_at, payload_hash, payload)
            select s.source_record_id, ds.source_id, r.ingest_run_id, s.source_key, s.url,
                   s.observed_at, s.effective_at, s.payload_hash, s.payload
            from stg_src s
            join data_source ds on ds.slug = s.slug
            join stg_runs r on r.slug = s.slug
            on conflict (source_id, source_key, payload_hash) do nothing""")
        conn.commit()
        print("source_record loaded")

        # ---- property
        cur.execute("""create temp table stg_prop (property_id uuid, jurisdiction_id text,
            source_parcel_id text, parcel_id_normalized text, situs_address text,
            situs_address_normalized text, city text, state text,
            lon double precision, lat double precision) on commit drop""")
        copy_rows(cur, "stg_prop",
                  ["property_id", "jurisdiction_id", "source_parcel_id", "parcel_id_normalized",
                   "situs_address", "situs_address_normalized", "city", "state", "lon", "lat"],
                  rows["property"])
        cur.execute("""insert into property (property_id, jurisdiction_id, source_parcel_id,
                parcel_id_normalized, situs_address, situs_address_normalized, city, state, centroid)
            select property_id, jurisdiction_id, source_parcel_id, parcel_id_normalized,
                   situs_address, situs_address_normalized, city, state,
                   case when lon is not null then ST_SetSRID(ST_MakePoint(lon, lat), 4326) end
            from stg_prop
            on conflict (jurisdiction_id, parcel_id_normalized) do update
              set situs_address = excluded.situs_address,
                  situs_address_normalized = excluded.situs_address_normalized,
                  city = excluded.city, centroid = excluded.centroid,
                  source_parcel_id = excluded.source_parcel_id,
                  last_seen_at = now()""")
        conn.commit()
        print("property loaded")

        # ---- snapshots
        cur.execute("""create temp table stg_snap (property_snapshot_id uuid, property_id uuid,
            source_record_id uuid, as_of_date date, owner_name_raw text, owner_name_normalized text,
            year_built int, stories numeric, assessor_sqft bigint, land_value numeric,
            improvement_value numeric, total_value numeric, assessed_value numeric,
            property_type text, legal_description text, subdivision text, lot text, block text,
            attributes jsonb) on commit drop""")
        copy_rows(cur, "stg_snap",
                  ["property_snapshot_id", "property_id", "source_record_id", "as_of_date",
                   "owner_name_raw", "owner_name_normalized", "year_built", "stories",
                   "assessor_sqft", "land_value", "improvement_value", "total_value",
                   "assessed_value", "property_type", "legal_description", "subdivision",
                   "lot", "block", "attributes"], rows["snapshot"])
        cur.execute("""insert into property_snapshot (property_snapshot_id, property_id,
                source_record_id, as_of_date, owner_name_raw, owner_name_normalized, year_built,
                stories, assessor_sqft, land_value, improvement_value, total_value, assessed_value,
                property_type, legal_description, subdivision, lot, block, attributes)
            select s.* from stg_snap s
            where exists (select 1 from source_record sr where sr.source_record_id = s.source_record_id)
            on conflict (property_id, source_record_id) do nothing""")
        conn.commit()
        print("property_snapshot loaded")

        # ---- entities + interests
        cur.execute("""create temp table stg_ent (entity_id uuid, entity_type text,
            display_name text, normalized_name text) on commit drop""")
        copy_rows(cur, "stg_ent", ["entity_id", "entity_type", "display_name", "normalized_name"],
                  rows["entity"])
        cur.execute("""insert into entity (entity_id, entity_type, display_name, normalized_name)
            select * from stg_ent on conflict (normalized_name) do nothing""")
        cur.execute("""create temp table stg_int (property_interest_id uuid, property_id uuid,
            entity_id uuid, role text, source_record_id uuid, confidence numeric) on commit drop""")
        copy_rows(cur, "stg_int", ["property_interest_id", "property_id", "entity_id", "role",
                                   "source_record_id", "confidence"], rows["interest"])
        cur.execute("""insert into property_interest (property_interest_id, property_id, entity_id,
                role, source_record_id, confidence)
            select i.property_interest_id, i.property_id, e.entity_id, i.role,
                   i.source_record_id, i.confidence
            from stg_int i
            join stg_ent se on se.entity_id = i.entity_id
            join entity e on e.normalized_name = se.normalized_name
            where exists (select 1 from source_record sr where sr.source_record_id = i.source_record_id)
            on conflict (property_id, entity_id, role, source_record_id) do nothing""")
        conn.commit()
        print("entity + property_interest loaded")

        # ---- buildings
        cur.execute("""create temp table stg_bld (building_id uuid, jurisdiction_id text,
            source_building_id text, property_id uuid, lon double precision, lat double precision,
            footprint_sqft numeric, is_primary_building boolean, year_built int,
            building_category text, stories numeric, assessor_sqft bigint,
            improvement_value numeric, match_method text, match_confidence numeric,
            source_record_id uuid) on commit drop""")
        copy_rows(cur, "stg_bld",
                  ["building_id", "jurisdiction_id", "source_building_id", "property_id",
                   "lon", "lat", "footprint_sqft", "is_primary_building", "year_built",
                   "building_category", "stories", "assessor_sqft", "improvement_value",
                   "match_method", "match_confidence", "source_record_id"], rows["building"])
        cur.execute("""insert into building (building_id, jurisdiction_id, source_building_id,
                property_id, centroid, footprint_sqft, is_primary_building, year_built,
                building_category, stories, assessor_sqft, improvement_value, match_method,
                match_confidence, source_record_id)
            select building_id, jurisdiction_id, source_building_id, property_id,
                   case when lon is not null then ST_SetSRID(ST_MakePoint(lon, lat), 4326) end,
                   footprint_sqft, is_primary_building, year_built, building_category, stories,
                   assessor_sqft, improvement_value, match_method, match_confidence,
                   source_record_id
            from stg_bld
            on conflict (jurisdiction_id, source_building_id) do update
              set property_id = excluded.property_id, match_method = excluded.match_method,
                  match_confidence = excluded.match_confidence,
                  year_built = excluded.year_built, improvement_value = excluded.improvement_value,
                  last_seen_at = now()""")
        conn.commit()
        print("building loaded")

        # ---- events + matches
        cur.execute("""create temp table stg_evt (event_id uuid, jurisdiction_id text,
            event_type text, event_subtype text, event_at date, observed_at timestamptz,
            title text, summary text, status text, amount numeric, source_record_id uuid,
            source_event_key text, lon double precision, lat double precision,
            attributes jsonb) on commit drop""")
        copy_rows(cur, "stg_evt",
                  ["event_id", "jurisdiction_id", "event_type", "event_subtype", "event_at",
                   "observed_at", "title", "summary", "status", "amount", "source_record_id",
                   "source_event_key", "lon", "lat", "attributes"], rows["event"])
        cur.execute("""insert into event (event_id, jurisdiction_id, event_type, event_subtype,
                event_at, observed_at, title, summary, status, amount, source_record_id,
                source_event_key, geometry, attributes)
            select e.event_id, e.jurisdiction_id, e.event_type, e.event_subtype, e.event_at,
                   e.observed_at, e.title, e.summary, e.status, e.amount, e.source_record_id,
                   e.source_event_key,
                   case when lon is not null then ST_SetSRID(ST_MakePoint(lon, lat), 4326) end,
                   e.attributes
            from stg_evt e
            where exists (select 1 from source_record sr where sr.source_record_id = e.source_record_id)
            on conflict (event_id) do update
              set status = excluded.status, event_at = excluded.event_at,
                  summary = excluded.summary, attributes = excluded.attributes,
                  source_record_id = excluded.source_record_id,
                  observed_at = excluded.observed_at""")
        cur.execute("""create temp table stg_match (event_id uuid, property_id uuid,
            match_method text, match_confidence numeric, is_primary boolean, evidence jsonb)
            on commit drop""")
        copy_rows(cur, "stg_match", ["event_id", "property_id", "match_method",
                                     "match_confidence", "is_primary", "evidence"], rows["match"])
        cur.execute("""insert into event_property_match (event_id, property_id, match_method,
                match_confidence, is_primary, evidence)
            select m.* from stg_match m
            where exists (select 1 from event e where e.event_id = m.event_id)
            on conflict (event_id, property_id) do update
              set match_method = excluded.match_method,
                  match_confidence = excluded.match_confidence""")
        conn.commit()
        print("event + event_property_match loaded")

        # ---- close runs + report
        counts = {}
        for t in ("property", "property_snapshot", "building", "entity",
                  "property_interest", "event", "event_property_match", "source_record"):
            cur.execute(f"select count(*) from {t}")
            counts[t] = cur.fetchone()[0]
        cur.execute("select pg_size_pretty(pg_database_size(current_database()))")
        size = cur.fetchone()[0]
        for slug, rid in run_ids.items():
            cur.execute("""update ingest_run set completed_at = now(), status = 'completed',
                           quality_report = %s where ingest_run_id = %s""",
                        (json.dumps(counts), rid))
        conn.commit()
    print("table counts:", counts)
    print("database size:", size)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None,
                    help="load only the first N parcels (smoke test)")
    ap.add_argument("--as-of", default=None, help="parcel-roll as-of date (YYYY-MM-DD)")
    ap.add_argument("--env-file", default=None)
    args = ap.parse_args()

    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    po, cama, bf, tp, permits, sr311, deeds = load_inputs(args.limit)
    rows, run_ids = build_rows(po, cama, bf, tp, permits, sr311, deeds, as_of)
    if args.dry_run:
        print("dry run — no database writes")
        return
    conn = connect(args.env_file)
    try:
        apply_migrations(conn)
        load(conn, rows, run_ids, as_of)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
