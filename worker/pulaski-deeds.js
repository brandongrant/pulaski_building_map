/**
 * Pulaski County recorded-documents proxy — Cloudflare Worker.
 *
 * The map is a static site and cannot read cross-origin PulaskiDeeds results,
 * so this Worker runs the county's property (legal-description) search
 * server-side and returns a clean, deduped JSON deed history the map renders
 * inline — including the CURRENT owner's chain of title with grantor/grantee
 * names, the WEBSTER FAMILY LIVING TRUST / 18 Toulouse case that owner-name
 * search can't reach.
 *
 * Why two steps: the property search is the only lot-filtered search, but its
 * result rows carry NO party names — those live on each document's details
 * page. The current owner's chain is always the most recent handful of
 * documents, so we date-window the search (fast: ~5 rows, ~3s instead of all
 * history) and fetch details only for those recent docs (in parallel, each on
 * its own session to dodge PHP session locking).
 *
 * GET /deeds?sub=<clerk SUB>&lot=<lot>&blc=<block>   -> JSON deed history
 * GET /health                                        -> {ok:true}
 *
 * Cached (Cache API, 7 days): repeat views are instant and the county site is
 * hit at most once per parcel per week — deliberately polite, only for live
 * parcels a user actually opens.
 */

const BASE = "https://pulaskideeds.com/search/";
const UA = "pulaski-building-map/1.0 (public-records research; github.com/brandongrant/pulaski_building_map)";
const CACHE_TTL = 604800;   // 7 days
const FETCH_MS = 20000;     // per-subrequest ceiling
const WINDOW_YEARS = 20;    // how far back the fast search looks for the current owner
const MAX_DETAILS = 8;      // cap party lookups (subrequest + time budget)

const INST_CODES = ["ALA","AMU","ARM","ARS","ASM","ASR","AST","ASU","BAS","BFD",
  "CAD","CCL","CNU","COD","COM","CRC","CRD","CRF","CSR","CTY","CVJ","DCH","DTM",
  "EAD","EXD","FJL","FTL","INT","IRD","IRL","IRM","IRT","LPL","LTD","MAD","MEL",
  "MGM","MID","MML","MRB","NJA","NJC","NJD","NJF","NJL","NJP","NJS","NOB","NOL",
  "OAO","ORS","ORU","OTB","OTC","OTD","OTI","OTJ","OTL","OUF","PAG","PLAT","POA",
  "PRL","PRM","PRR","PRU","PSJ","QCD","RAR","RBA","RDD","REL","REM","REU","RML",
  "RPA","RTL","SAJ","SALE","SML","SUM","SUS","SUT","SUU","TEU","TMU","UCC","WAD"];

// ownership-transfer deeds (used to pick the current owner); excludes DEED OF
// TRUST, which is a mortgage, and the release/assignment "…DEED" edge cases
const OWNERSHIP_RE = /\bDEED\b/;
const NOT_OWNERSHIP_RE = /DEED OF TRUST|RELEASE|ASSIGNMENT|SUBORDINAT|IN RELATION/;

/* ------------------------------------------------------------ parsing */
function stripTags(s) {
  return String(s).replace(/<[^>]+>/g, " ").replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&").replace(/\s+/g, " ").trim();
}

function parseLegal(cellHtml) {
  const flat = stripTags(cellHtml);
  const out = {};
  const re = /([A-Z][A-Z ]*?):\s*([^:]*?)(?=\s+[A-Z][A-Z ]*?:|$)/g;
  let m;
  while ((m = re.exec(flat))) {
    const k = m[1].trim(), v = m[2].trim();
    if (k && v) out[k] = v;
  }
  return out;
}

// property (legal) search result: date, inst, type, legal — NO parties
function parseProperty(html) {
  const tb = html.match(/<tbody[^>]*>([\s\S]*?)<\/tbody>/i);
  if (!tb) return [];
  const byInst = new Map();
  const rowRe = /<tr[^>]*>([\s\S]*?)<\/tr>/gi;
  let row;
  while ((row = rowRe.exec(tb[1]))) {
    const cells = [...row[1].matchAll(/<td[^>]*>([\s\S]*?)<\/td>/gi)].map((c) => c[1]);
    if (cells.length < 4) continue;
    const txt = cells.map(stripTags);
    const rd = (txt[0].match(/\d{8}/) || [""])[0];
    const inst = (txt[1].split(/\s+/)[0] || "");
    if (!inst || !/^\d/.test(inst) || byInst.has(inst)) continue;
    const img = cells[8] && cells[8].match(/view_image\.php\?key=([a-f0-9]+)/i);
    byInst.set(inst, {
      inst, date: rd, type: txt[2], legal: parseLegal(cells[3]),
      image: img ? img[1] : null,
    });
  }
  return [...byInst.values()].sort((a, b) => (b.date > a.date ? 1 : b.date < a.date ? -1 : 0));
}

// details page: grantor (Party 1) + grantee (Party 2), names split on <br/>
function parseDetail(html) {
  const m = html.match(/Party 1([\s\S]*?)(?:Legal Description|Cross Ref)/i);
  if (!m) return { grantor: [], grantee: [] };
  const cells = [...m[1].matchAll(/<td[^>]*>([\s\S]*?)<\/td>/gi)]
    .map((c) => c[1])
    .filter((c) => { const t = stripTags(c); return t && !/^Party\s*\d/i.test(t); });
  const names = (c) => {
    const out = [];
    for (const x of String(c).split(/<br\s*\/?>/i)) {
      const t = stripTags(x);
      if (t && !out.includes(t)) out.push(t);
    }
    return out;
  };
  return { grantor: names(cells[0] || ""), grantee: names(cells[1] || "") };
}

/* ---- fuzzy party matching so "WEBSTER CODY" ~ "WEBSTER CODY BRANDON" ---- */
function nameKey(n) {
  return new Set(String(n).toUpperCase().replace(/[^A-Z0-9 ]+/g, " ").split(/\s+/).filter(Boolean));
}
function partiesOverlap(a, b) {
  const ka = nameKey(a), kb = nameKey(b);
  if (!ka.size || !kb.size) return false;
  let shared = 0;
  for (const t of ka) if (kb.has(t)) shared++;
  return shared >= Math.min(ka.size, kb.size); // whole shorter name contained
}

/* ------------------------------------------------------------ live fetch */
async function fetchWithTimeout(url, opts) {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), FETCH_MS);
  try {
    return await fetch(url, { ...opts, signal: ctl.signal, cf: { cacheTtl: 0 } });
  } finally {
    clearTimeout(t);
  }
}

async function newSession() {
  const idx = await fetchWithTimeout(BASE + "index.php", { headers: { "User-Agent": UA } });
  const cookie = (idx.headers.get("set-cookie") || "").match(/PHPSESSID=[^;]+/);
  if (!cookie) throw new Error("no session cookie");
  return cookie[0];
}

function mdy(d) {
  return `${String(d.getUTCMonth() + 1).padStart(2, "0")}/${String(d.getUTCDate()).padStart(2, "0")}/${d.getUTCFullYear()}`;
}

async function propertySearch(cookie, sub, lot, blc, startMDY, endMDY) {
  const form = new URLSearchParams();
  form.set("searchType", "property");
  form.set("LOT", lot);
  form.set("BLC", blc && blc !== "0" ? blc : "");
  for (const k of ["RNG", "SEC", "QTR", "TWP", "PD", "TRCT", "UNIT", "BLD", "PH", "CON"]) form.set(k, "");
  form.set("SUB", sub);
  form.set("prop_start_date", startMDY);
  form.set("prop_end_date", endMDY);
  form.set("instType[ALL]", "ALL");
  for (const c of INST_CODES) form.set(`instType[${c}]`, c);
  const res = await fetchWithTimeout(BASE + "content.php", {
    method: "POST",
    headers: {
      "User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded",
      "Cookie": cookie, "Referer": BASE + "index.php",
    },
    body: form.toString(),
  });
  const html = await res.text();
  if (html.includes("County and/or state")) throw new Error("session not established");
  return parseProperty(html);
}

// each detail on its OWN session so the fetches truly run in parallel
async function fetchDetail(inst) {
  try {
    const cookie = await newSession();
    const res = await fetchWithTimeout(
      BASE + `content.php?searchType=details&noImage=1&inst_num=${encodeURIComponent(inst)}`,
      { headers: { "User-Agent": UA, "Cookie": cookie, "X-Requested-With": "XMLHttpRequest", "Referer": BASE + "index.php" } });
    return parseDetail(await res.text());
  } catch (e) {
    return { grantor: [], grantee: [] };
  }
}

async function buildHistory(sub, lot, blc) {
  const now = new Date();
  const start = new Date(Date.UTC(now.getUTCFullYear() - WINDOW_YEARS, 0, 1));
  const cookie = await newSession();
  const docs = await propertySearch(cookie, sub, lot, blc, mdy(start), mdy(now));

  const recent = docs.slice(0, MAX_DETAILS);
  const details = await Promise.all(recent.map((d) => fetchDetail(d.inst)));
  recent.forEach((d, i) => { d.grantor = details[i].grantor; d.grantee = details[i].grantee; });

  // current owner = grantee of the most recent ownership-transfer deed
  let owner = [];
  for (const d of recent) {
    if (OWNERSHIP_RE.test(d.type) && !NOT_OWNERSHIP_RE.test(d.type) && d.grantee && d.grantee.length) {
      owner = d.grantee;
      break;
    }
  }
  for (const d of docs) {
    if (d.grantor || d.grantee) {
      const parties = [...(d.grantor || []), ...(d.grantee || [])];
      d.chain = owner.length > 0 && parties.some((p) => owner.some((o) => partiesOverlap(p, o)));
    }
  }
  return { owner, since: start.getUTCFullYear(), docs };
}

/* ------------------------------------------------------------ HTTP glue */
function cors(resp) {
  resp.headers.set("Access-Control-Allow-Origin", "*");
  resp.headers.set("Access-Control-Allow-Methods", "GET, OPTIONS");
  resp.headers.set("Access-Control-Max-Age", "86400");
  return resp;
}
function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status, headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}

export default {
  async fetch(request) {
    const url = new URL(request.url);
    if (request.method === "OPTIONS") return cors(new Response(null, { status: 204 }));
    if (url.pathname === "/health") return cors(json({ ok: true }));
    if (url.pathname !== "/deeds") return cors(json({ error: "not found" }, 404));

    const sub = (url.searchParams.get("sub") || "").trim();
    const lot = (url.searchParams.get("lot") || "").trim();
    const blc = (url.searchParams.get("blc") || "").trim();
    if (!sub || !lot) return cors(json({ error: "sub and lot required" }, 400));

    const cache = caches.default;
    const cacheKey = new Request(url.toString());
    const cached = await cache.match(cacheKey);
    if (cached) return cached;

    try {
      const out = await buildHistory(sub, lot, blc);
      const resp = cors(json({
        sub, lot, blc, ...out,
        count: out.docs.length,
        chain: out.docs.filter((d) => d.chain).length,
      }));
      resp.headers.set("Cache-Control", `public, max-age=${CACHE_TTL}`);
      try { await cache.put(cacheKey, resp.clone()); } catch (e) {}
      return resp;
    } catch (e) {
      const resp = cors(json({ error: String((e && e.message) || e), docs: [] }, 502));
      resp.headers.set("Cache-Control", "public, max-age=120"); // brief negative cache
      return resp;
    }
  },
};

// exported for the Node parser test (unused in the Worker runtime)
export { parseProperty, parseDetail, partiesOverlap };
