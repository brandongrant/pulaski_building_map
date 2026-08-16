"""Ingest a public record about a surveillance program and file it in the paper trail.

Give it a URL (or a local file) and it fetches the document, extracts the text,
pulls out the facts that answer "how much, who authorized it, out of which
account", and appends an entry to the registry.

    python pipeline/surveillance_docs.py add <url-or-path> --program flock-lrpd
    python pipeline/surveillance_docs.py add <url> --program shotspotter --title "..."
    python pipeline/surveillance_docs.py queue web-submissions.json
    python pipeline/surveillance_docs.py list
    python pipeline/surveillance_docs.py rebuild      # re-run extraction on cached text

Registry:  pipeline/surveillance/documents.json   (committed - the trail itself)
Text:      pipeline/surveillance/doc_text/<id>.txt (committed - the evidence)
Original:  data/raw/surveillance_docs/<id>.<ext>   (gitignored - re-fetchable)

Extraction is deliberately conservative: every fact it reports is a literal
string found in the document, and the full text is kept next to the entry so a
reader can check it. Anything it cannot find stays empty rather than guessed.
"""
import argparse
import hashlib
import io
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common.settings import RAW_DIR  # noqa: E402

SRC = Path(__file__).parent / "surveillance"
TEXT_DIR = SRC / "doc_text"
REGISTRY = SRC / "documents.json"
CACHE = RAW_DIR / "surveillance_docs"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# Vendors and resellers that show up in Pulaski County surveillance records.
VENDORS = [
    "Flock Group", "Flock Safety", "Insight Public Sector", "ShotSpotter",
    "SoundThinking", "Axon", "Motorola Solutions", "Genetec", "Leonardo",
    "ELSAG", "Verkada", "Avigilon", "LiveView Technologies", "Rekor",
    "Iteris", "Wavetronix", "TAPCO", "Omnia Partners", "OMNIA Partners",
    "NCPA", "Sourcewell", "Fusus", "Axis Communications",
]

MONTHS = ("January|February|March|April|May|June|July|August|September|"
          "October|November|December")

RE_MONEY = re.compile(r"\$\s?([\d,]+(?:\.\d{2})?)")
RE_ORDINANCE = re.compile(r"Ordinance\s+No\.?\s*([\d][\d,\.]{2,})", re.I)
RE_ACCOUNT = re.compile(r"[Aa]ccount\s+(?:No\.?\s*)?([\d]{5,6}-[\d]{4,6})")
RE_COOP = re.compile(r"(Omnia|OMINA|NCPA|Sourcewell)(?:\s+Partners)?"
                     r"(?:\s+Contract)?\s*(?:No\.?)?\s*#?\s*(\d[\d\- ]{4,}\d)", re.I)
# Clerks write "Resolution No. 16,489", "Resolution 15,392" and
# "Resolutions 15,844 and 16,202" - so read the whole clause, then take
# every five-digit number out of it.
RE_RES_CLAUSE = re.compile(r"Resolutions?\s+(?:Nos?\.?\s*)?"
                           r"((?:\d[\d,\.]{3,}\s*(?:,|and|&|\s)\s*)*\d[\d,\.]{3,})", re.I)
RE_RES_NUM = re.compile(r"\d{2}[,.]\d{3}")
RE_DATE = re.compile(rf"({MONTHS})\s+(\d{{1,2}}),?\s+(\d{{4}})")
RE_TERM = re.compile(r"(\w+)\s*\(\s*(\d+)\s*\)\s*[- ]?\s*year", re.I)
RE_AGENDA_URL = re.compile(r"(\d{1,2})-(\d{1,2})-(20\d{2})")
MONTH_NUM = {m: i + 1 for i, m in enumerate(
    "January February March April May June July August September October "
    "November December".split())}


def fetch(target):
    """Return (bytes, content_type, resolved_url) for a URL or local path."""
    if re.match(r"^https?://", target, re.I):
        req = urllib.request.Request(target, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.read(), r.headers.get("Content-Type", ""), r.geturl()
    p = Path(target)
    if not p.exists():
        raise SystemExit(f"not a URL and not a file: {target}")
    kind = "application/pdf" if p.suffix.lower() == ".pdf" else "text/plain"
    return p.read_bytes(), kind, p.resolve().as_uri()


def to_text(raw, content_type, url):
    """Extract plain text from a PDF, HTML page, or text file."""
    is_pdf = raw[:5] == b"%PDF-" or "pdf" in content_type.lower() or url.lower().endswith(".pdf")
    if is_pdf:
        import pdfplumber  # imported lazily: only PDFs need it
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            pages = [(p.extract_text() or "") for p in pdf.pages]
        return "\n".join(pages), "pdf", len(pages)
    text = raw.decode("utf-8", errors="replace")
    if "<html" in text[:2000].lower() or "html" in content_type.lower():
        text = re.sub(r"(?is)<(script|style|nav|footer|head)\b.*?</\1>", " ", text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = (text.replace("&amp;", "&").replace("&nbsp;", " ")
                    .replace("&#8217;", "'").replace("&quot;", '"'))
        return re.sub(r"[ \t]+", " ", text).strip(), "html", 1
    return text, "text", 1


def money_values(text):
    """Dollar figures as (numeric, literal) pairs, largest first, deduped."""
    seen, out = set(), []
    for m in RE_MONEY.finditer(text):
        literal = m.group(0).replace("$ ", "$")
        try:
            value = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        if value < 100 or value in seen:      # skip line numbers and page refs
            continue
        seen.add(value)
        out.append({"value": value, "literal": literal})
    return sorted(out, key=lambda d: -d["value"])[:12]


def uniq(seq):
    out = []
    for x in seq:
        if x not in out:
            out.append(x)
    return out


def guess_kind(text):
    head = text[:600].upper()
    if "BOARD OF DIRECTORS COMMUNICATION" in head:
        return "board_communication"
    if head.lstrip().startswith("1 RESOLUTION") or "RESOLUTION NO" in head:
        return "resolution"
    if "ORDINANCE NO" in head:
        return "ordinance"
    if "MINUTES" in head:
        return "minutes"
    if "INVOICE" in head:
        return "invoice"
    if "AGREEMENT" in head or "CONTRACT" in head:
        return "contract"
    return "record"


def guess_body(text, url):
    head = text[:900].upper()
    if "LITTLE ROCK" in head and "BOARD OF DIRECTORS" in head:
        return "Little Rock Board of Directors"
    if "NORTH LITTLE ROCK" in head:
        return "North Little Rock City Council"
    if "QUORUM COURT" in head:
        return "Pulaski County Quorum Court"
    if "ARDOT" in head or "ARKANSAS DEPARTMENT OF TRANSPORTATION" in head:
        return "Arkansas Department of Transportation"
    host = urllib.parse.urlparse(url).netloc.lower()
    return {"www.littlerock.gov": "City of Little Rock",
            "littlerock.gov": "City of Little Rock",
            "ardot.gov": "Arkansas Department of Transportation",
            "www.ardot.gov": "Arkansas Department of Transportation"}.get(host, "")


def guess_date(text, url):
    """Prefer the agenda date in the URL, else the first date in the header."""
    m = RE_AGENDA_URL.search(urllib.parse.unquote(url))
    if m:
        mo, day, yr = (int(x) for x in m.groups())
        if 1 <= mo <= 12 and 1 <= day <= 31:
            return f"{yr:04d}-{mo:02d}-{day:02d}"
    m = RE_DATE.search(text[:1200])
    if m:
        return f"{int(m.group(3)):04d}-{MONTH_NUM[m.group(1)]:02d}-{int(m.group(2)):02d}"
    return ""


def guess_title(text, url):
    """Agenda memos say it after 'Subject:'; resolutions say 'A RESOLUTION TO ...'."""
    flat = re.sub(r"^\s*\d+\s+", "", text, flags=re.M)        # strip line numbers
    flat = re.sub(r"\s+", " ", flat)
    m = re.search(r"\bA (RESOLUTION|ORDINANCE) TO (.+?)(?:;| AND FOR OTHER PURPOSES|\.)",
                  flat, re.I)
    if m:
        title = f"A {m.group(1).lower()} to {m.group(2).strip()}"
        return re.sub(r"\s+", " ", title)[:200]
    m = re.search(r"Subject:\s*(.+?)(?:Submitted By:|SYNOPSIS)", flat, re.I)
    if m:
        subject = re.sub(r"Action Required:|Approved By:|Ordinance|Resolution|[√✓]",
                         " ", m.group(1))
        subject = re.sub(r"\s+", " ", subject).strip(" .")
        if len(subject) > 15:
            return subject[:200]
    for line in text.splitlines():
        line = line.strip()
        if len(line) > 25 and not line.isupper():
            return line[:200]
    return urllib.parse.unquote(Path(urllib.parse.urlparse(url).path).stem)[:200]


def section(text, label, stop_labels):
    """Pull an agenda-memo block such as SYNOPSIS / FISCAL IMPACT / BACKGROUND."""
    stops = "|".join(stop_labels)
    m = re.search(rf"{label}\s+(.*?)(?=\n\s*(?:{stops})\b|\Z)", text, re.S)
    if not m:
        return ""
    return re.sub(r"\s+", " ", m.group(1)).strip()[:900]


LABELS = ["SYNOPSIS", "FISCAL IMPACT", "RECOMMENDATION", "BACKGROUND", "ADOPTED"]


def extract(text, url):
    """Everything the document says about money and authorization.

    Facts are matched against a whitespace-flattened copy so a value broken
    across a line ("#23-6692-\\n03") still reads as one string.
    """
    flat = re.sub(r"\s+", " ", text)
    vendors = uniq([v for v in VENDORS if v.lower() in flat.lower()])
    coop = [f"{m.group(1).title().replace('Omina', 'Omnia')} Partners "
            f"{m.group(2).replace(' ', '')}" for m in RE_COOP.finditer(flat)]
    terms = [f"{m.group(2)}-year" for m in RE_TERM.finditer(flat)]
    # Clerks type both "15,892" and "15.892" for the same resolution.
    resolutions = uniq(n.replace(".", ",") for clause in RE_RES_CLAUSE.findall(flat)
                       for n in RE_RES_NUM.findall(clause))
    facts = {
        "resolutions": resolutions,
        "ordinances": uniq(RE_ORDINANCE.findall(flat)),
        "accounts": uniq(RE_ACCOUNT.findall(flat)),
        "cooperative_contracts": uniq(coop),
        "vendors": vendors,
        "terms": uniq(terms),
    }
    parts = {
        "synopsis": section(text, "SYNOPSIS", LABELS),
        "fiscal_impact": section(text, "FISCAL IMPACT", LABELS),
        "background": section(text, "BACKGROUND", LABELS),
    }
    return facts, {k: v for k, v in parts.items() if v}


def load_registry():
    if REGISTRY.exists():
        return json.loads(REGISTRY.read_text(encoding="utf-8"))
    return []


def save_registry(docs):
    docs.sort(key=lambda d: (d.get("date") or "", d.get("id") or ""))
    SRC.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps(docs, indent=1, ensure_ascii=False) + "\n",
                        encoding="utf-8")


def make_id(url, title):
    stem = Path(urllib.parse.unquote(urllib.parse.urlparse(url).path)).stem or title
    slug = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")[:52]
    return f"{slug or 'doc'}-{hashlib.sha256(url.encode()).hexdigest()[:6]}"


def add(args):
    docs = load_registry()
    raw, content_type, url = fetch(args.target)
    if args.url:                              # local file that came from a known URL
        url = args.url
    sha = hashlib.sha256(raw).hexdigest()
    if not args.force:
        for d in docs:
            if d.get("sha256") == sha:
                print(f"already filed as {d['id']} ({d.get('title', '')[:60]})")
                return

    text, fmt, pages = to_text(raw, content_type, url)
    if len(text.strip()) < 40:
        print("WARNING: almost no text extracted - scanned image? "
              "Filing it anyway with the facts you passed on the command line.")
    facts, parts = extract(text, url)
    doc_id = args.id or make_id(url, args.title or "")

    CACHE.mkdir(parents=True, exist_ok=True)
    TEXT_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE / f"{doc_id}.{'pdf' if fmt == 'pdf' else 'txt'}").write_bytes(raw)
    (TEXT_DIR / f"{doc_id}.txt").write_text(text, encoding="utf-8")

    entry = {
        "id": doc_id,
        "title": args.title or guess_title(text, url),
        "date": args.date or guess_date(text, url),
        "body": args.body or guess_body(text, url),
        "kind": args.kind or guess_kind(text),
        "programs": args.program or [],
        "url": url,
        "source": urllib.parse.urlparse(url).netloc,
        "format": fmt,
        "pages": pages,
        "sha256": sha,
        "retrieved": time.strftime("%Y-%m-%d"),
        "added_by": args.by,
        "amounts": money_values(text),
        "facts": facts,
        "parts": parts,
        "excerpt": re.sub(r"\s+", " ", text).strip()[:1500],
    }
    if args.note:
        entry["note"] = args.note
    docs = [d for d in docs if d["id"] != doc_id] + [entry]
    save_registry(docs)

    print(f"filed {doc_id}")
    print(f"  title    {entry['title'][:90]}")
    print(f"  date     {entry['date'] or '(unknown)'}   body: {entry['body'] or '(unknown)'}")
    print(f"  kind     {entry['kind']}   programs: {', '.join(entry['programs']) or '(none)'}")
    if entry["amounts"]:
        print(f"  money    {', '.join(a['literal'] for a in entry['amounts'][:6])}")
    for key, val in facts.items():
        if val:
            print(f"  {key:<22} {', '.join(map(str, val))[:100]}")
    if not entry["programs"]:
        print("  NOTE: no --program given, so it will not attach to any device."
              " Re-run with --program to link it.")


def queue(args):
    """Ingest submissions exported from the site's 'add to the trail' form."""
    items = json.loads(Path(args.file).read_text(encoding="utf-8"))
    if isinstance(items, dict):
        items = items.get("submissions", [])
    print(f"{len(items)} submission(s) queued")
    for i, item in enumerate(items, 1):
        target = item.get("url") or item.get("file")
        if not target:
            print(f"[{i}] skipped - no url or file")
            continue
        print(f"[{i}] {target}")
        try:
            add(argparse.Namespace(
                target=target, url=item.get("url"), program=item.get("programs", []),
                title=item.get("title"), date=item.get("date"), body=item.get("body"),
                kind=item.get("kind"), note=item.get("note"),
                by=item.get("added_by", "submitted"), id=None, force=False))
        except Exception as exc:                       # keep going through the queue
            print(f"    FAILED: {exc}")


def show(args):
    docs = load_registry()
    print(f"{len(docs)} document(s) in the trail\n")
    for d in docs:
        money = d["amounts"][0]["literal"] if d.get("amounts") else ""
        print(f"{d.get('date', ''):<11} {money:>12}  {', '.join(d.get('programs', [])):<18} "
              f"{d.get('title', '')[:64]}")
        print(f"{'':<11} {'':>12}  {d['id']}")


def rebuild(args):
    """Re-run fact extraction over the committed text (no network)."""
    docs = load_registry()
    for d in docs:
        path = TEXT_DIR / f"{d['id']}.txt"
        if not path.exists():
            print(f"  no text cached for {d['id']} - skipped")
            continue
        text = path.read_text(encoding="utf-8")
        d["facts"], d["parts"] = extract(text, d.get("url", ""))
        d["amounts"] = money_values(text)
        d["excerpt"] = re.sub(r"\s+", " ", text).strip()[:1500]
    save_registry(docs)
    print(f"re-extracted {len(docs)} document(s)")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="fetch a URL or file and file it")
    a.add_argument("target")
    a.add_argument("--program", action="append", default=[],
                   help="program id to attach to (repeatable)")
    a.add_argument("--title")
    a.add_argument("--date", help="YYYY-MM-DD (else read from the document)")
    a.add_argument("--body", help="authorizing body (else read from the document)")
    a.add_argument("--kind", help="resolution/board_communication/contract/invoice/...")
    a.add_argument("--note", help="why this document matters")
    a.add_argument("--url", help="original URL when target is a downloaded file")
    a.add_argument("--id", help="override the generated id")
    a.add_argument("--by", default="research", help="who found it")
    a.add_argument("--force", action="store_true", help="re-file even if unchanged")
    a.set_defaults(func=add)

    q = sub.add_parser("queue", help="ingest a submissions file from the site")
    q.add_argument("file")
    q.set_defaults(func=queue)

    sub.add_parser("list", help="show the trail").set_defaults(func=show)
    sub.add_parser("rebuild", help="re-extract facts from cached text").set_defaults(func=rebuild)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
