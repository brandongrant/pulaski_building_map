"""Search North Little Rock's public council record and pull document text.

Little Rock publishes agenda items as PDFs on its own web server. North Little
Rock instead puts its whole council record - ordinances, resolutions, minutes,
agenda packages - in a public Laserfiche portal, which has no PDF endpoint but
does expose full-text search and a per-page text layer.

    python pipeline/nlr_laserfiche.py search "SkyCop"
    python pipeline/nlr_laserfiche.py text 630141

Used by surveillance_docs.py, which recognises a portal.laserfiche.com DocView
link and files it like any other record, so the citation stays a public URL.
"""
import argparse
import html
import http.cookiejar
import json
import re
import sys
import urllib.request

REPO = "r-caf858ef"                       # City of North Little Rock
BASE = "https://portal.laserfiche.com/Portal/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

DOCVIEW = re.compile(r"portal\.laserfiche\.com/Portal/DocView\.aspx\?id=(\d+)"
                     r"(?:&repo=([\w-]+))?", re.I)

_opener = None


def session():
    """Laserfiche hands out a PublicPortalSession cookie on first browse."""
    global _opener
    if _opener is None:
        jar = http.cookiejar.CookieJar()
        _opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        _opener.addheaders = [("User-Agent", UA)]
        _opener.open(f"{BASE}browse.aspx?repo={REPO}", timeout=60).read()
    return _opener


def post(path, payload):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": UA,
                 "Accept": "application/json", "X-Lf-Suppress-Login-Redirect": "1"})
    return json.load(session().open(req, timeout=90))


def is_laserfiche(url):
    return bool(DOCVIEW.search(url or ""))


def doc_id(url):
    m = DOCVIEW.search(url)
    return int(m.group(1)) if m else None


def doc_url(entry_id, repo=REPO):
    return f"{BASE}DocView.aspx?id={entry_id}&repo={repo}"


def search(term, limit=30, repo=REPO):
    """Full-text search. Returns [{name, id, date, kind, hits[]}]."""
    data = post("SearchService.aspx/GetSearchListing", {
        "repoName": repo,
        "searchSyn": '{LF:Basic ~= "%s", option="DFANLT"}' % term.replace('"', ""),
        "searchUuid": None, "sortColumn": "", "startIdx": 0, "endIdx": limit,
        "getNewListing": True, "sortOrder": 2, "displayInGridView": False})
    out = []
    for r in data.get("data", {}).get("results", []):
        meta = {m["name"]: (m["values"] or [""])[0] for m in (r.get("metadata") or [])}
        out.append({
            "name": r.get("name", ""),
            "id": r.get("entryId"),
            "date": meta.get("Date", ""),
            "number": meta.get("Number", ""),
            "kind": (r.get("entryProperties") or "").strip(),
            "pages": r.get("thumbnailPageCount") or 0,
            "url": doc_url(r.get("entryId"), repo),
            "hits": [re.sub(r"\s+", " ", h.get("Context", "")).strip()
                     for h in (r.get("contexthits") or [])],
        })
    return out


def page_text(entry_id, page, repo=REPO):
    data = post("DocumentService.aspx/GetTextHtmlForPage", {
        "repoName": repo, "documentId": int(entry_id), "pageNum": int(page),
        "showAnn": True, "searchUuid": ""})
    raw = data.get("data")
    if isinstance(raw, dict):
        raw = raw.get("text") or raw.get("html") or ""
    if not raw:
        return ""
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", str(raw))
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    return html.unescape(text)


def document_text(entry_id, repo=REPO, max_pages=40):
    """Concatenate the text layer of every page."""
    pages, out = [], []
    for page in range(1, max_pages + 1):
        try:
            body = page_text(entry_id, page, repo)
        except Exception:
            break
        if not body.strip():
            if page > 1:
                break
            continue
        out.append(body.strip())
        pages.append(page)
    text = "\n".join(out)
    # The viewer's text layer wraps hard; join hyphen breaks and squeeze blanks.
    text = re.sub(r"-\n(\w)", r"\1", text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip(), len(pages)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search", help="full-text search the council record")
    s.add_argument("term")
    s.add_argument("--limit", type=int, default=30)
    t = sub.add_parser("text", help="print a document's text")
    t.add_argument("entry_id", type=int)
    args = ap.parse_args()

    if args.cmd == "search":
        rows = search(args.term, args.limit)
        print(f"{len(rows)} hit(s) for {args.term!r}\n")
        for r in rows:
            print(f"{r['date']:<11} {r['name']:<24} id={r['id']:<8} {r['kind']}")
            for h in r["hits"][:2]:
                print(f"            …{h[:96]}…")
            print(f"            {r['url']}")
    else:
        text, pages = document_text(args.entry_id)
        sys.stdout.write(f"[{pages} page(s)]\n{text}\n")


if __name__ == "__main__":
    main()
