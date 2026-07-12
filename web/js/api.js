/* Pulaski County Building Map — external record services: PulaskiDeeds
   hand-offs, ARCountyData/Treasurer links, and the deed-history proxy Worker */
import { esc } from "./util.js";

export const PULASKI_DEEDS_BASE = "https://pulaskideeds.com/search/";
// index.php gates everything behind a legal-disclaimer page and only marks
// the session accepted itself — but it honors Accept via GET, so this URL
// lands users on a working search page in one hop instead of the disclaimer.
export const PULASKI_DEEDS_ACCEPT_URL = `${PULASKI_DEEDS_BASE}index.php?Accept=Accept`;
// Deployed Cloudflare Worker that returns a parcel's deed history as JSON
// (worker/pulaski-deeds.js). The URL lives in data/services.json — separate
// from the pipeline-generated config.json so a data refresh can't clobber it.
// Empty = feature off (the deeds link falls back to the plain PulaskiDeeds
// hand-off).
let DEEDS_API = "";
// Property-profile API (worker/profile-api over the Phase 1 database). Same
// contract: URL lives in data/services.json, empty = feature off.
let PROFILE_API = "";
const ARCOUNTY_PARCEL_BASE = "https://www.arcountydata.com/parcel.asp?County=Pulaski&ParcelID=";
const TREASURER_MOBILE_BASE = "https://public.pulaskicountytreasurer.net/mobile/pulaski/";
const PULASKI_INST_CODES = [
  "ALA", "AMU", "ARM", "ARS", "ASM", "ASR", "AST", "ASU", "BAS", "BFD",
  "CAD", "CCL", "CNU", "COD", "COM", "CRC", "CRD", "CRF", "CSR", "CTY",
  "CVJ", "DCH", "DTM", "EAD", "EXD", "FJL", "FTL", "INT", "IRD", "IRL",
  "IRM", "IRT", "LPL", "LTD", "MAD", "MEL", "MGM", "MID", "MML", "MRB",
  "NJA", "NJC", "NJD", "NJF", "NJL", "NJP", "NJS", "NOB", "NOL", "OAO",
  "ORS", "ORU", "OTB", "OTC", "OTD", "OTI", "OTJ", "OTL", "OUF", "PAG",
  "PLAT", "POA", "PRL", "PRM", "PRR", "PRU", "PSJ", "QCD", "RAR", "RBA",
  "RDD", "REL", "REM", "REU", "RML", "RPA", "RTL", "SAJ", "SALE", "SML",
  "SUM", "SUS", "SUT", "SUU", "TEU", "TMU", "UCC", "WAD",
];

// Runtime service endpoints. Optional — the map works without it, deed
// history just stays off. Loaded in parallel with config.json; DEEDS_API is
// only read on popup open, well after both fetches settle.
export function loadServices() {
  return fetch("data/services.json")
    .then((r) => (r.ok ? r.json() : null))
    .then((s) => {
      if (s?.services?.deeds_api && s.features?.deed_history !== false) {
        DEEDS_API = s.services.deeds_api.replace(/\/+$/, "");
      }
      if (s?.services?.profile_api && s.features?.property_profiles !== false) {
        PROFILE_API = s.services.profile_api.replace(/\/+$/, "");
      }
    })
    .catch(() => {});
}

export function profileApi() {
  return PROFILE_API;
}

export function parcelIdForURL(parcelId) {
  return String(parcelId || "").trim();
}

function treasurerParcelId(parcelId) {
  return String(parcelId || "").replace(/[^0-9A-Za-z]/g, "");
}

export function arCountyParcelURL(parcelId) {
  return ARCOUNTY_PARCEL_BASE + encodeURIComponent(parcelIdForURL(parcelId));
}

export function treasurerURL(parcelId, address) {
  const cleanParcel = treasurerParcelId(parcelId);
  const u = new URL("tax-open.html", location.href);
  if (cleanParcel) u.searchParams.set("parcel", cleanParcel);
  if (parcelId) u.searchParams.set("display", parcelIdForURL(parcelId));
  if (address) u.searchParams.set("address", String(address).trim());
  return u.href;
}

function pulaskiInst(inst) {
  return String(inst || "").replace(/[^0-9A-Za-z-]/g, "");
}

function pulaskiDeedContentURL() {
  return `${PULASKI_DEEDS_BASE}content.php?embedded=1&${Date.now()}`;
}

function pulaskiOpenURL(key, value) {
  const u = new URL("pulaski-open.html", location.href);
  u.searchParams.set(key, value);
  return u.href;
}

// First-party loading screen shown in the popup while PulaskiDeeds is fetched,
// so the user sees immediate feedback instead of a blank page.
const PULASKI_SPINNER_MS = 1500;

function pulaskiLoadingURL(kind, value) {
  const u = new URL("deeds-open.html", location.href);
  u.searchParams.set(kind === "inst" ? "inst" : "owner", value);
  return u.href;
}

function pulaskiWindowName(kind) {
  return `pulaski_${kind}_${Date.now()}_${Math.floor(Math.random() * 100000)}`;
}

function postPulaskiWindow(target, fields) {
  const form = document.createElement("form");
  form.method = "post";
  form.action = `${PULASKI_DEEDS_BASE}ajaxActions.php`;
  form.target = target;
  form.hidden = true;
  for (const [name, value] of Object.entries(fields)) {
    const input = document.createElement("input");
    input.type = "hidden";
    input.name = name;
    input.value = value;
    form.appendChild(input);
  }
  document.body.appendChild(form);
  form.submit();
  window.setTimeout(() => form.remove(), 2500);
}

export function openPulaskiDeed(inst) {
  const clean = pulaskiInst(inst);
  if (!clean) return false;
  const target = pulaskiWindowName("inst");
  // Show our loading screen first; then drive PulaskiDeeds in the same window.
  const w = window.open(pulaskiLoadingURL("inst", clean), target);
  if (!w) return false;
  // Two top-level GETs: accept the disclaimer (which also establishes the
  // session), then ask content.php for the record directly — it takes the
  // details params in the query string. Top-level GETs always carry the
  // session cookie under SameSite=Lax, unlike the cross-site POST dance this
  // used to rely on, which browsers increasingly refuse to attach cookies to.
  window.setTimeout(() => {
    try { w.location.href = PULASKI_DEEDS_ACCEPT_URL; } catch (e) {}
  }, PULASKI_SPINNER_MS);
  window.setTimeout(() => {
    try {
      w.location.href = `${PULASKI_DEEDS_BASE}content.php?searchType=details&inst_num=${encodeURIComponent(clean)}`;
      w.focus();
    } catch (e) {}
  }, PULASKI_SPINNER_MS + 5500);
  return true;
}

function pulaskiOwnerEntityId(owner) {
  const clean = String(owner || "").toUpperCase().replace(/[^A-Z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  return clean ? clean + ":" : "";
}

function pulaskiOwnerValue(owner) {
  return String(owner || "").toUpperCase().replace(/[^A-Z0-9]+/g, "");
}

function pulaskiOwnerCandidates(owner) {
  const raw = String(owner || "").split(/[\/;]+/);
  const out = [];
  for (const part of raw) {
    const clean = part.replace(/\s+/g, " ").trim();
    if (clean && !out.some((v) => v.toUpperCase() === clean.toUpperCase())) out.push(clean);
  }
  return out.length ? out : [String(owner || "").trim()].filter(Boolean);
}

function pulaskiTodayMDY() {
  const d = new Date();
  return `${String(d.getMonth() + 1).padStart(2, "0")}/${String(d.getDate()).padStart(2, "0")}/${d.getFullYear()}`;
}

function pulaskiOwnerDataString(names) {
  const primary = names[names.length - 1];
  const data = new URLSearchParams();
  data.set("searchType", "name");
  data.set("start_date", "01/01/1903");
  data.set("end_date", pulaskiTodayMDY());
  data.set("sort_type", "Name");
  data.set("search_type", "Standard");
  data.set("last_name", primary.toLowerCase());
  data.set("party_type", "Both");
  data.set("entity_type", "Both");
  data.set("instType[ALL][ALL]", "ALL");
  for (const code of PULASKI_INST_CODES) data.set(`instType[${code}][${code}]`, code);
  data.set("plresults_length", "100");
  for (const name of names) data.append("name[]", pulaskiOwnerValue(name));
  return data.toString();
}

export function openPulaskiOwnerIndex(owner) {
  const names = pulaskiOwnerCandidates(owner);
  if (!names.length || !pulaskiOwnerDataString(names)) return false;
  const target = pulaskiWindowName("owner");
  // Show our loading screen first; then drive PulaskiDeeds in the same window.
  const w = window.open(pulaskiLoadingURL("owner", String(owner || "")), target);
  if (!w) return false;
  // Land on the accept URL so the session is past the disclaimer before the
  // storeEID/storeDataString POSTs — un-accepted sessions get rejected.
  window.setTimeout(() => {
    try { w.location.href = PULASKI_DEEDS_ACCEPT_URL; } catch (e) {}
  }, PULASKI_SPINNER_MS);
  names.forEach((name, i) => {
    window.setTimeout(() => {
      postPulaskiWindow(target, { entityID: pulaskiOwnerEntityId(name), action: "storeEID" });
    }, PULASKI_SPINNER_MS + 1100 + (i * 900));
  });
  window.setTimeout(() => {
    postPulaskiWindow(target, { dataString: pulaskiOwnerDataString(names), action: "storeDataString" });
  }, PULASKI_SPINNER_MS + 1100 + (names.length * 900));
  window.setTimeout(() => {
    try {
      w.location.href = pulaskiDeedContentURL();
      w.focus();
    } catch (e) {}
  }, PULASKI_SPINNER_MS + 11000 + (names.length * 1200));
  return true;
}

export function deedDocLink(inst, label) {
  const clean = pulaskiInst(inst);
  if (!clean) return "";
  return `<a class="doc-link" data-pulaski-inst="${clean}" href="${pulaskiOpenURL("inst", clean)}" target="_blank" rel="noopener" ` +
    `title="Open PulaskiDeeds document details for instrument ${clean}">` +
    `${esc(label || clean)}</a>`;
}

export function deedOwnerLink(owner, label) {
  const enc = encodeURIComponent(String(owner || ""));
  if (!enc) return "";
  return `<a class="doc-link" data-pulaski-owner="${enc}" href="${pulaskiOpenURL("owner", String(owner || ""))}" target="_blank" rel="noopener" ` +
    `title="Open PulaskiDeeds records indexed to ${esc(owner)}">` +
    `${esc(label || owner)}</a>`;
}

document.addEventListener("click", (e) => {
  const a = e.target && e.target.closest ? e.target.closest("a[data-pulaski-inst],a[data-pulaski-owner]") : null;
  if (!a) return;
  const handled = a.dataset.pulaskiInst
    ? openPulaskiDeed(a.dataset.pulaskiInst)
    : openPulaskiOwnerIndex(decodeURIComponent(a.dataset.pulaskiOwner || ""));
  if (handled) e.preventDefault();
});

/* -------------------------------- live deed history via the proxy Worker */
const DEED_CACHE = new Map(); // legal key -> promise (dedupe/reuse within a session)

export function deedHistoryAvailable(parcel) {
  return !!(DEEDS_API && parcel && parcel.sub && parcel.lot);
}

// resolve a parcel's deed history from the Worker; one in-flight/settled
// promise per legal key
export function fetchDeedHistory(parcel) {
  const key = `${parcel.sub}|${parcel.lot}|${parcel.blc || ""}`;
  let p = DEED_CACHE.get(key);
  if (!p) {
    const u = `${DEEDS_API}/deeds?sub=${encodeURIComponent(parcel.sub)}` +
      `&lot=${encodeURIComponent(parcel.lot)}&blc=${encodeURIComponent(parcel.blc || "")}`;
    p = fetch(u).then((r) => r.json()).catch(() => ({ error: true, docs: null }));
    DEED_CACHE.set(key, p);
  }
  return p;
}
