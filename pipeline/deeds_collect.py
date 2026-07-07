"""Collect Pulaski County recorded documents (pulaskideeds.com) incrementally.

Designed to run on a GitHub Actions runner (stdlib + requests only), sharing
the dispatch workflow's data-branch checkout.

Usage: python pipeline/deeds_collect.py --store <data-branch-checkout-dir>
                                        [--max-queries N] [--start YYYY-MM-DD]

How the source behaves (measured 2026-07-06, docs/recorded_documents_plan.md):
  - session: POST Accept=Accept to /search/index.php, keep the cookie, read
    the per-session `random` token from the form page
  - a search = POST the serialized form to ajaxActions.php (storeDataString),
    then GET content.php?embedded=1&<rand> with XHR headers; results render
    server-side into a #results table (one row per document PER PARTY SIDE)
  - cost is ~1 s per result row with a hard ~180 s server cap, so queries
    must stay small: one recording DAY x one TYPE GROUP at a time
  - the verified index lags recording by ~2-4 weeks (the Temp Index is the
    in-process queue), so recent days legitimately return 0 rows and must be
    re-checked later

Politeness budget: --max-queries per run (default 2) + one session bootstrap;
with the */15 cron that is ~200 light requests/day, comparable to one human
user. Days that keep failing (server cap) are retried across runs with an
attempt counter, never hammered in a loop.

Store layout:
  deeds/legal_index.json.gz   SUBDIV|LOT|BLOCK -> [lon, lat, addr, city]
                              (pipeline/build_legal_index.py, committed once)
  deeds/state.json            per (date, group) row counts / attempts
  deeds/raw/YYYY-MM.jsonl     append-only document archive (dedupe: inst_num)
  deeds/out/recent_activity.geojson  matched docs, last 365 d
  deeds/out/stats.json

Privacy: military discharges (DCH) and medical-record authorizations (ARM)
are never requested — they are not property records.
"""
import argparse
import gzip
import json
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

import requests

BASE = "https://pulaskideeds.com/search/"
UA = {"User-Agent": "pulaski-building-map/1.0 (public-records research; "
                    "github.com/brandongrant/pulaski_building_map)"}
XHR = {"X-Requested-With": "XMLHttpRequest", "Referer": BASE + "index.php"}

# instrument-type groups, sized so a busy day stays under the ~180 s server
# cap (~150 rows). Codes from the search form's own pick list.
GROUPS = {
    "deed1": ["WAD", "LTD"],
    "deed2": ["QCD", "BFD", "COD", "EXD", "CRD", "CAD", "RDD", "MAD", "MID",
              "NJD", "OTD", "IRD", "SALE"],
    "mtg1": ["MGM"],
    "mtg2": ["DTM", "COM", "IRM", "INT"],
    "rel1": ["REM"],
    "rel2": ["REU", "PRM", "PRU", "IRT", "RAR", "PRR", "CSR", "CRF", "RTL",
             "REL", "PRL", "RML", "SAJ", "PSJ"],
    "asgn": ["ASM", "ASR", "ARS", "AST", "ASU", "ALA", "SUM", "SUU", "SUT",
             "NJA", "NJC", "NJP", "NJS"],
    "lien": ["MML", "MRB", "MEL", "SML", "FTL", "CCL", "CVJ", "FJL", "OTJ",
             "OTL", "NOL", "IRL", "NJL", "LPL", "NJF", "OUF"],
    "misc": ["PLAT", "BAS", "RBA", "ORS", "SUS", "EAD", "OTC", "POA", "RPA",
             "PAG", "OTB", "NOB", "OTI", "CTY", "UCC", "CNU", "TEU", "TMU",
             "AMU", "OAO", "ORU"],
}
# normalized category per code (map/legend buckets)
CODE_CAT = {}
for _g, _codes in [("deed", GROUPS["deed1"] + GROUPS["deed2"]),
                   ("mtg", GROUPS["mtg1"] + GROUPS["mtg2"]),
                   ("rel", GROUPS["rel1"] + GROUPS["rel2"]),
                   ("asgn", GROUPS["asgn"])]:
    for _c in _codes:
        CODE_CAT[_c] = _g
for _c in ["MML", "MRB", "MEL", "SML", "FTL", "CCL", "CVJ", "FJL", "OTJ",
           "OTL", "NOL", "IRL", "NJL"]:
    CODE_CAT[_c] = "lien"
for _c in ["LPL", "NJF", "OUF"]:
    CODE_CAT[_c] = "fcl"          # foreclosure-adjacent
for _c in ["PLAT", "BAS", "RBA", "ORS", "SUS"]:
    CODE_CAT[_c] = "plat"
for _c in ["EAD", "OTC"]:
    CODE_CAT[_c] = "ease"
for _c in GROUPS["misc"]:
    CODE_CAT.setdefault(_c, "oth")

RECHECK_EMPTY_DAYS = 4     # empty day+group: try again this often
STABLE_AFTER_DAYS = 45     # a day this old with rows is considered complete
RECHECK_DONE_AFTER = 21    # one late re-check for days that already had rows
MAX_ATTEMPTS = 6

WS = re.compile(r"\s+")


def norm(s):
    return WS.sub(" ", re.sub(r"[^A-Z0-9 ]+", " ", str(s or "").upper())).strip()


STOP_TOKENS = {"ADN", "ADDN", "ADDITION", "SUB", "SUBD", "SUBDIVISION",
               "REPLAT", "REPL", "RPLT", "PLAT", "PH", "PHASE", "AMENDED",
               "AMD", "REV", "REVISED", "TRACT", "TR", "PT", "PART", "HPR"}
ROMAN = re.compile(r"^(?=[IVX])I{0,3}(?:V|X)?I{0,3}$")


def base_name(subdiv_norm):
    toks = [t for t in subdiv_norm.split(" ") if t and t not in STOP_TOKENS]
    while len(toks) > 1 and (toks[-1].isdigit() or ROMAN.match(toks[-1])
                             or (len(toks[-1]) == 1 and len(toks) > 2)):
        toks.pop()
    return " ".join(toks) if toks else subdiv_norm


def lb_norm(v):
    return str(v or "").lstrip("0") or ""


def match_legal(lidx, legal):
    """-> ([lon, lat, addr, city], quality) or (None, None).
    Tiers: exact subdiv -> suffix-stripped base -> unique lot|block bucket
    entry whose base name contains/is contained by the document's."""
    sn = norm(legal.get("SUBDIVISION"))
    if not sn:
        return None, None
    lot, blk = lb_norm(legal.get("LOT")), lb_norm(legal.get("BLOCK"))
    for k in (f"{sn}|{lot}|{blk}", f"{sn}|{lot}|"):
        hit = lidx.get("exact", {}).get(k)
        if hit:
            return hit, "exact"
    bn = base_name(sn)
    for k in (f"{bn}|{lot}|{blk}", f"{bn}|{lot}|"):
        hit = lidx.get("base", {}).get(k)
        if hit:
            return hit, "base"
    if lot:
        cands = [c for c in lidx.get("lb", {}).get(f"{lot}|{blk}", [])
                 if c[0].startswith(bn) or bn.startswith(c[0])]
        if len({(c[1], c[2]) for c in cands}) == 1:
            return cands[0][1:], "prefix"
    return None, None


# ---------------------------------------------------------------- source
def open_session():
    s = requests.Session()
    s.headers.update(UA)
    s.get(BASE + "index.php", timeout=60)
    r = s.post(BASE + "index.php", data={"Accept": "Accept"}, timeout=60)
    m = re.search(r'name="random" value="(\d+)"', r.text)
    if not m:
        raise RuntimeError("pulaskideeds disclaimer flow changed (no random token)")
    return s, m.group(1)


def query_day(s, rand, day_mdY, codes):
    """One (day, type-group) search. Returns list of party-side row dicts,
    or None if the server hit its execution cap (retry later)."""
    fields = [("searchType", "instrumenttype"), ("random", rand),
              ("start_date_instrumenttype", day_mdY),
              ("end_date_instrumenttype", day_mdY)]
    fields += [(f"instType[{c}]", c) for c in codes]
    r = s.post(BASE + "ajaxActions.php",
               data={"dataString": urlencode(fields), "action": "storeDataString"},
               headers=XHR, timeout=60)
    r.raise_for_status()
    r = s.get(BASE + f"content.php?embedded=1&0.{int(time.time())}",
              headers=XHR, timeout=210)
    r.raise_for_status()
    if "<tbody" not in r.text:
        return None                      # 92-byte cap/error page
    return parse_rows(r.text)


CELL_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
TAG_RE = re.compile(r"<[^>]+>")
KV_RE = re.compile(r"([A-Z ]+):\s*([^<\n]*?)\s*(?=<br|[A-Z ]+:|$)")


def _names(cell_html):
    out = []
    for part in re.split(r"<br\s*/?>", cell_html, flags=re.I):
        t = WS.sub(" ", TAG_RE.sub(" ", part).replace("&nbsp;", " ")).strip()
        if t:
            out.append(t)
    return out


def parse_rows(html):
    tb = re.search(r"<tbody[^>]*>(.*?)</tbody>", html, re.S)
    rows = []
    for tr in ROW_RE.findall(tb.group(1)):
        cells = CELL_RE.findall(tr)
        if len(cells) < 7:
            continue
        txt = [WS.sub(" ", TAG_RE.sub(" ", c).replace("&nbsp;", " ")).strip()
               for c in cells]
        legal = {}
        for k, v in KV_RE.findall(cells[3].replace("&nbsp;", " ")):
            k, v = k.strip(), WS.sub(" ", TAG_RE.sub(" ", v)).strip()
            if k and v:
                legal[k] = v
        rows.append({
            "rd": re.sub(r"\D", "", txt[0])[:8],
            "inst": txt[1].split()[0] if txt[1].split() else "",
            "dtype": txt[2],
            "legal": legal,
            "side": "1" if "1" in txt[4] else "2",
            "searched": _names(cells[5]),
            "reverse": _names(cells[6]),
        })
    return rows


def merge_docs(rows, code_by_label):
    """party-side rows -> one record per instrument number"""
    docs = {}
    for r in rows:
        if not r["inst"]:
            continue
        d = docs.setdefault(r["inst"], {
            "inst": r["inst"], "rd": r["rd"], "dtype": r["dtype"],
            "code": code_by_label.get(r["dtype"], ""),
            "grantor": [], "grantee": [], "legal": {},
        })
        d["legal"].update(r["legal"])
        gr, ge = (r["searched"], r["reverse"]) if r["side"] == "1" \
            else (r["reverse"], r["searched"])
        for n in gr:
            if n not in d["grantor"]:
                d["grantor"].append(n)
        for n in ge:
            if n not in d["grantee"]:
                d["grantee"].append(n)
    return list(docs.values())


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--max-queries", type=int, default=2)
    ap.add_argument("--start", default="2026-04-01",
                    help="earliest recording date to harvest")
    args = ap.parse_args()
    store = Path(args.store) / "deeds"
    raw_dir = store / "raw"
    out_dir = store / "out"
    raw_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    # instType code -> on-screen label ("WAD" -> "WARRANTY DEED") mapping is
    # only needed in reverse; grid shows labels
    labels = json.loads((Path(__file__).parent / "deeds_inst_codes.json").read_text())
    code_by_label = {v: k for k, v in labels.items()}

    state_f = store / "state.json"
    state = json.loads(state_f.read_text()) if state_f.exists() else {}

    today = datetime.now(timezone.utc).date()
    start = date.fromisoformat(args.start)

    # ---------------- pick work: oldest actionable (day, group) first
    work = []
    d = start
    while d <= today:
        iso = d.isoformat()
        age = (today - d).days
        for g in GROUPS:
            st = state.get(f"{iso}:{g}", {})
            att, rows_n = st.get("att", 0), st.get("rows", -1)
            checked = st.get("ts", "")
            if att >= MAX_ATTEMPTS and rows_n < 0:
                continue
            if rows_n < 0:                       # never succeeded
                work.append((iso, g))
            elif rows_n == 0:                    # empty: index may lag
                if age <= STABLE_AFTER_DAYS and \
                        checked < (today - timedelta(days=RECHECK_EMPTY_DAYS)).isoformat():
                    work.append((iso, g))
            elif not st.get("final"):            # had rows: one late re-check
                if age >= RECHECK_DONE_AFTER and \
                        checked < (d + timedelta(days=RECHECK_DONE_AFTER)).isoformat():
                    work.append((iso, g))
        d += timedelta(days=1)
    work = work[:args.max_queries]
    print(f"queue: {len(work)} of budget {args.max_queries}")

    new_docs = []
    if work:
        s, rand = open_session()
        for iso, g in work:
            day_mdY = datetime.fromisoformat(iso).strftime("%m/%d/%Y")
            key = f"{iso}:{g}"
            st = state.setdefault(key, {})
            st["att"] = st.get("att", 0) + 1
            st["ts"] = today.isoformat()
            t0 = time.time()
            try:
                rows = query_day(s, rand, day_mdY, GROUPS[g])
            except Exception as e:
                print(f"  {key}: ERROR {e}", file=sys.stderr)
                rows = None
            if rows is None:
                print(f"  {key}: server cap/error after {time.time() - t0:.0f}s "
                      f"(attempt {st['att']})")
                continue
            docs = merge_docs(rows, code_by_label)
            st["rows"] = len(rows)
            st["docs"] = len(docs)
            st["att"] = 0
            age = (today - date.fromisoformat(iso)).days
            if len(rows) and age >= RECHECK_DONE_AFTER:
                st["final"] = 1
            if age > STABLE_AFTER_DAYS and not len(rows):
                st["final"] = 1                  # old and empty: accept
            print(f"  {key}: {len(rows)} rows -> {len(docs)} docs "
                  f"in {time.time() - t0:.0f}s")
            new_docs += docs
            time.sleep(3)

    # ---------------- archive merge (dedupe by inst within each month file)
    if new_docs:
        by_month = {}
        for doc in new_docs:
            m = f"{doc['rd'][:4]}-{doc['rd'][4:6]}" if len(doc["rd"]) == 8 else "unknown"
            by_month.setdefault(m, []).append(doc)
        for m, docs in by_month.items():
            f = raw_dir / f"{m}.jsonl"
            seen = {}
            if f.exists():
                for line in f.read_text(encoding="utf-8").splitlines():
                    try:
                        seen[json.loads(line)["inst"]] = line
                    except Exception:
                        pass
            for doc in docs:
                doc["seen"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                seen[doc["inst"]] = json.dumps(doc, separators=(",", ":"))
            f.write_text("\n".join(seen.values()) + "\n", encoding="utf-8")
    state_f.write_text(json.dumps(state, separators=(",", ":")), encoding="utf-8")

    # ---------------- outputs: match to parcels, last-365d activity layer
    lidx = {}
    li_f = store / "legal_index.json.gz"
    if li_f.exists():
        with gzip.open(li_f, "rt", encoding="utf-8") as f:
            lidx = json.load(f)

    horizon = (today - timedelta(days=365)).strftime("%Y%m%d")
    feats, total, matched, earliest = [], 0, 0, None
    for f in sorted(raw_dir.glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                doc = json.loads(line)
            except Exception:
                continue
            total += 1
            rd = doc.get("rd", "")
            if earliest is None or (rd and rd < earliest):
                earliest = rd
            hit, mq = match_legal(lidx, doc.get("legal", {}))
            if hit:
                matched += 1
            if not hit or rd < horizon:
                continue
            feats.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [hit[0], hit[1]]},
                "properties": {
                    "d": int(rd), "t": doc.get("code", ""),
                    "c": CODE_CAT.get(doc.get("code", ""), "oth"),
                    "dt": doc.get("dtype", "").title(),
                    "g1": "; ".join(doc.get("grantor", [])[:4]).title(),
                    "g2": "; ".join(doc.get("grantee", [])[:4]).title(),
                    "a": norm(hit[2]), "n": doc["inst"], "mq": mq,
                },
            })
    (out_dir / "recent_activity.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": feats},
                   separators=(",", ":")), encoding="utf-8")
    pend = sum(1 for k, v in state.items() if v.get("rows", -1) < 0)
    (out_dir / "stats.json").write_text(json.dumps({
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_documents": total,
        "earliest": earliest,
        "match_rate": round(matched / total, 3) if total else None,
        "recent_matched": len(feats),
        "pending_day_groups": pend,
    }), encoding="utf-8")
    print(f"archive: {total} docs, matched {matched} "
          f"({matched / total * 100:.0f}%), recent layer {len(feats)} pts, "
          f"pending {pend} day-groups" if total else "archive empty")


if __name__ == "__main__":
    main()
