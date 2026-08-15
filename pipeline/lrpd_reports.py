"""Parse a City of Little Rock daily police report PDF into incident records.

The city publishes a PDF each weekday at
https://littlerock.gov/residents/police-department/21st-century-policing/daily-reports/
Each PDF is a handful of complete LRPD incident-report packets (Form 5501)
concatenated — every packet is a fixed-layout form whose first page carries the
incident header and whose later pages carry suspect/victim/property blocks and
a free-text narrative.

What this module extracts, and what it deliberately does not
-----------------------------------------------------------
Published fields are all *incident-level*: number, date/time, call type,
statutory offenses, police district and the incident address. The narrative is
read but never returned unless the caller explicitly asks for it
(``keep_narrative=True``, used only by local debugging) — those paragraphs name
victims, witnesses and suspects, and re-publishing them is a different act from
the city posting a PDF. Instead the narrative is reduced to a small set of
mechanical tags (weapon present, entry forced, property taken, …) that describe
the event, never a person.

Parsing notes
-------------
* Field positions on the header page are fixed to the point, so values are read
  geometrically from boxes anchored under each printed label rather than from
  text order (which the form's two-column layout scrambles).
* A red "Redact Before Release" stamp is drawn *over* the header on some
  reports; its characters interleave with real values. Non-black characters are
  dropped before words are assembled.
* Some daily PDFs are scanned images with no text layer, and some are mixed
  (image cover page, digital narrative). Whatever can be recovered is returned
  with ``parsed`` set to ``full`` or ``partial`` so the collector can tell the
  difference between "nothing happened" and "could not read it".
"""
import re

import pdfplumber

COVER_MARK = "LITTLE ROCK POLICE DEPARTMENT INCIDENT REPORT"
INCIDENT_RE = re.compile(r"\b(20\d\d-\d{6})\b")
PAGE_OF_RE = re.compile(r"Page (\d+) of (\d+)")

# Header value boxes: (x0, top, x1, bottom) in PDF points, each anchored just
# below its printed label. Verified identical across sampled reports.
HEADER_BOXES = {
    "no":        (28, 72, 214, 98),
    "dt":        (28, 108, 252, 142),
    "unit":      (216, 72, 296, 98),
    "call_date": (297, 72, 400, 98),
    "call_time": (401, 72, 480, 98),
    "call_type": (481, 72, 596, 98),
    "loc":       (256, 108, 518, 150),
    "district":  (519, 108, 596, 150),
}
# The offense list is two numbered columns of statute names.
OFFENSE_BOXES = ((36, 193, 240, 252), (247, 193, 452, 252))

# LRPD CAD call-type mnemonics seen on the header. Unknown codes fall back to a
# tidied version of the code itself, so a new mnemonic degrades to readable text
# instead of disappearing.
CALL_TYPES = {
    "BATTERY": "Battery",
    "SHOOTP": "Shooting in progress",
    "SHOOTJ": "Shooting just occurred",
    "SHOTS": "Shots fired",
    "SHOTSP": "Shots fired in progress",
    "ROBBIN": "Robbery — individual",
    "ROBBUS": "Robbery — business",
    "ROBBP": "Robbery in progress",
    "DISWP": "Disturbance with a weapon",
    "DIST": "Disturbance",
    "BURGRES": "Burglary — residence",
    "BURGCOM": "Burglary — commercial",
    "BURG": "Burglary",
    "THEFT": "Theft",
    "THEFTP": "Theft in progress",
    "AUTOTHEFT": "Auto theft",
    "HOMICIDE": "Homicide",
    "STABBING": "Stabbing",
    "RAPE": "Sexual assault",
    "ASSAULT": "Assault",
    "DOMESTIC": "Domestic disturbance",
    "MISSING": "Missing person",
    "ARSON": "Arson",
    "FRAUD": "Fraud",
    "CARJACK": "Carjacking",
}

# Narrative -> mechanical tags. Ordered only for readability; all are tested.
# Nothing here describes a person: no names, ages, sex, race or condition.
TAG_RULES = [
    ("firearm",   r"\bFIREARM|\bGUN\b|HANDGUN|PISTOL|RIFLE|SHOTGUN|SHOT ?S?\b|SHOOTING|SHELL CASING|DISCHARGE"),
    ("knife",     r"\bKNIFE|STABB|CUTTING INSTRUMENT|\bBLADE"),
    ("forced_entry", r"FORCED ENTRY|PRIED|KICKED IN|BROKE (THE |A )?(WINDOW|DOOR|GLASS)|SHATTERED|FORCED (THE )?(DOOR|WINDOW)"),
    ("property_taken", r"\bSTOLE\b|\bSTOLEN\b|TOOK HIS|TOOK HER|TOOK THE|REMOVED THE|UNPAID MERCHANDISE|DEMANDED"),
    ("vehicle_involved", r"\bVEHICLE\b|\bSUV\b|\bTRUCK\b|LICENSE PLATE"),
    ("injury",    r"\bINJUR|\bWOUND|BLEEDING|LACERATION|TRANSPORTED TO (THE )?(HOSPITAL|UAMS|BAPTIST)|AMBULANCE|\bMEMS\b"),
    ("arrest",    r"\bARREST|TAKEN INTO CUSTODY|TRANSPORTED TO (THE )?(JAIL|PULASKI COUNTY)"),
    ("business",  r"\bBUSINESS\b|\bSTORE\b|EMPLOYEE|REGISTER|\bMANAGER\b|RESTAURANT|GAS STATION"),
    ("residence", r"\bRESIDENCE\b|\bAPARTMENT|\bHOME\b|\bHOUSE\b"),
    ("camera",    r"SURVEILLANCE|\bCAMERA|\bVIDEO FOOTAGE|RING DOORBELL"),
    ("fled",      r"\bFLED\b|\bFLEEING\b|RAN (NORTH|SOUTH|EAST|WEST|AWAY|OFF)|LEFT (THE )?(SCENE|AREA)|\bON FOOT\b"),
    ("suspect_unknown", r"UNKNOWN (BLACK |WHITE |HISPANIC )?(MALE|FEMALE|SUBJECT|SUSPECT|PERSON)|NO SUSPECTS? (WERE |COULD BE )?(LOCATED|IDENTIFIED)|UNABLE TO LOCATE"),
]
TAG_RE = [(k, re.compile(p)) for k, p in TAG_RULES]

TAG_LABELS = {
    "firearm": "firearm", "knife": "knife", "forced_entry": "forced entry",
    "property_taken": "property taken", "vehicle_involved": "vehicle involved",
    "injury": "injury reported", "arrest": "arrest made", "business": "at a business",
    "residence": "at a residence", "camera": "camera footage", "fled": "suspect fled",
    "suspect_unknown": "suspect unknown",
}

# Address-shaped text inside a narrative, used only when the header page is a
# scan and the incident address could not be read directly.
NARR_ADDR_RE = re.compile(
    r"(?:RESPONDED TO|DISPATCHED TO|LOCATED AT|CALLED TO|ARRIVED AT|SCENE AT)\s+"
    r"(?:THE\s+)?(\d{1,5}\s+[A-Z0-9][A-Z0-9 .'-]{3,34}?)"
    r"(?=\s+(?:IN|FOR|DUE|REGARDING|REFERENCE|ON|AND|AT|WHERE|TO|,|\.))")


def _black_words(page):
    """Words on the page with the red redaction stamp's characters removed."""
    def keep(obj):
        if obj["object_type"] != "char":
            return True
        c = obj.get("non_stroking_color")
        if isinstance(c, (list, tuple)) and len(c) == 3:
            r, g, b = c
            if r > 0.5 and g < 0.4 and b < 0.4:      # the stamp is pure red
                return False
        return True

    try:
        return page.filter(keep).extract_words()
    except Exception:
        return page.extract_words()


def _in_box(words, box):
    """Words whose centre falls in ``box``, read top-to-bottom, left-to-right."""
    x0, top, x1, bottom = box
    hits = []
    for w in words:
        cx, cy = (w["x0"] + w["x1"]) / 2, (w["top"] + w["bottom"]) / 2
        if x0 <= cx <= x1 and top <= cy <= bottom:
            hits.append((round(w["top"] / 4), w["x0"], w["text"]))
    hits.sort()
    return hits


def _box_text(words, box):
    return " ".join(t for _, _, t in _in_box(words, box)).strip()


def _box_lines(words, box):
    lines, out = {}, []
    for row, x, txt in _in_box(words, box):
        lines.setdefault(row, []).append((x, txt))
    for row in sorted(lines):
        line = " ".join(t for _, t in sorted(lines[row])).strip()
        if line:
            out.append(line)
    return out


def _norm_dt(date_s, time_s):
    """('8/13/2026 5:41:57 PM', '17:41:00') -> '2026-08-13T17:41' (local)."""
    m = re.search(r"(\d{1,2})/(\d{1,2})/(20\d\d)(?:\s+(\d{1,2}):(\d\d)(?::\d\d)?\s*([AP]M)?)?",
                  date_s or "")
    if not m:
        return None
    mo, dy, yr, hh, mi, ap = m.groups()
    if hh is None:
        t = re.match(r"(\d{1,2}):(\d\d)", (time_s or "").strip())
        hh, mi = (t.group(1), t.group(2)) if t else ("00", "00")
    hh, mi = int(hh), int(mi)
    if ap == "PM" and hh < 12:
        hh += 12
    elif ap == "AM" and hh == 12:
        hh = 0
    return f"{int(yr):04d}-{int(mo):02d}-{int(dy):02d}T{hh:02d}:{mi:02d}"


def _clean_loc(s):
    s = " ".join((s or "").split()).upper()
    s = re.sub(r"\b(REDACT|BEFORE|RELEASE)\b", "", s).strip()
    return s


def tags_for(text):
    return [k for k, rx in TAG_RE if rx.search(text)]


def call_type_label(code):
    code = (code or "").strip().upper()
    if not code:
        return ""
    return CALL_TYPES.get(code, code.title())


def _header(words):
    out = {k: _box_text(words, b) for k, b in HEADER_BOXES.items()}
    m = INCIDENT_RE.search(out["no"])
    out["no"] = m.group(1) if m else ""
    # the incident stamp is the precise one; the call date/time is the fallback
    out["dt"] = _norm_dt(out["dt"], out["call_time"]) or \
        _norm_dt(out["call_date"], out["call_time"])
    out.pop("call_date", None)
    out.pop("unit", None)
    out["loc"] = _clean_loc(out["loc"])
    out["district"] = re.sub(r"\D", "", out["district"])[:3]
    out["call_type"] = re.sub(r"[^A-Z0-9/ -]", "", out["call_type"].upper()).strip()
    offs = []
    for box in OFFENSE_BOXES:
        for line in _box_lines(words, box):
            line = re.sub(r"^\d+\.\s*", "", line).strip()
            if len(line) > 3 and not line.isdigit():
                offs.append(line)
    out["offenses"] = offs
    out.pop("call_time", None)
    return out


def parse(fileobj, keep_narrative=False):
    """Parse one daily-report PDF.

    Returns ``{"pages": n, "text_pages": n, "incidents": [record, ...]}`` where
    each record has: no, dt, call_type, call_type_label, offenses, district,
    loc, loc_src, tags, pdf_page (1-based cover page), parsed ('full' |
    'partial'). ``narrative`` is present only when ``keep_narrative`` is set and
    is never written to any published file.
    """
    covers, narratives, text_pages, gen_dt = {}, {}, 0, None
    with pdfplumber.open(fileobj) as pdf:
        pages = len(pdf.pages)
        for i, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            if len(text.strip()) > 40:
                text_pages += 1
            if gen_dt is None:
                g = re.search(r"Report generated:?\s*(\d{1,2}/\d{1,2}/20\d\d)", text)
                if g:
                    gen_dt = g.group(1)
            if COVER_MARK in text:
                words = _black_words(page)
                h = _header(words)
                span = PAGE_OF_RE.search(text)
                h["_span"] = int(span.group(2)) if span else 1
                h["pdf_page"] = i + 1
                if h["no"]:
                    covers[h["no"]] = h
            elif "NARRATIVE" in text:
                m = INCIDENT_RE.search(text)
                body = " ".join(text.split("NARRATIVE", 1)[1].split())
                # strip the page furniture that the form prints after the text
                body = re.split(r"Page \d+ of \d+|Redact Before Release", body)[0]
                if m and len(body) > 40:
                    narratives.setdefault(m.group(1), []).append((i + 1, body))

    out = []
    for no in sorted(set(covers) | set(narratives)):
        narr = " ".join(b for _, b in narratives.get(no, []))
        rec = covers.get(no)
        if rec:
            rec = {k: v for k, v in rec.items() if not k.startswith("_")}
            rec["parsed"] = "full"
            rec["loc_src"] = "header" if rec["loc"] else ""
        else:
            rec = {"no": no, "dt": None, "call_type": "", "offenses": [],
                   "district": "", "loc": "", "loc_src": "",
                   "pdf_page": narratives[no][0][0], "parsed": "partial"}
        if not rec["loc"] and narr:
            m = NARR_ADDR_RE.search(narr)
            if m:
                rec["loc"] = _clean_loc(m.group(1))
                rec["loc_src"] = "narrative"
        rec["call_type_label"] = call_type_label(rec["call_type"])
        rec["tags"] = tags_for(narr) if narr else []
        # A scanned cover page costs us the incident stamp; the packet's
        # "Report generated" date still dates the incident to within a day or
        # two, so keep it and mark the day as approximate.
        gen_iso = _norm_dt(gen_dt, None)
        rec["date"] = (rec["dt"] or gen_iso or "")[:10] or None
        rec["date_exact"] = bool(rec["dt"])
        if keep_narrative:
            rec["narrative"] = narr
        out.append(rec)

    out.sort(key=lambda r: (r["dt"] or "", r["no"]))
    return {"pages": pages, "text_pages": text_pages, "incidents": out}


if __name__ == "__main__":            # pragma: no cover - manual inspection aid
    import json
    import sys

    for path in sys.argv[1:]:
        with open(path, "rb") as f:
            print(path)
            print(json.dumps(parse(f), indent=2)[:4000])
