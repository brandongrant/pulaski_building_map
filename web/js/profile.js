/* Pulaski County Building Map — property-profile drawer (roadmap §6.5).
   Fed by the worker/profile-api service over the Phase 1 database; the URL
   comes from data/services.json (empty = feature off, nothing renders).
   Building popups add a "View property profile" action; the drawer is also
   shareable via the `property=` hash param (#map=…&property=<id>). */
import { $, fmt, fmtUSD, esc } from "./util.js";
import { map } from "./map.js";
import { profileApi } from "./api.js";

const EVENT_LABELS = {
  ownership_transfer: "Ownership",
  mortgage_recorded: "Mortgage",
  mortgage_released: "Mortgage released",
  lien_recorded: "Lien",
  foreclosure_notice: "Foreclosure",
  easement_recorded: "Easement",
  plat_recorded: "Plat",
  document_recorded: "Document",
  permit_issued: "Permit",
  demolition_permit: "Demolition",
  unsafe_vacant_status: "Unsafe/vacant",
  service_request: "311",
};

let drawer = null;
const cache = new Map(); // key -> profile promise

function hashParams() {
  return new URLSearchParams(location.hash.replace(/^#/, ""));
}

function setHashProperty(id) {
  const p = hashParams();
  if (id) p.set("property", id); else p.delete("property");
  const s = p.toString();
  // replaceState keeps drawer opens/closes out of the back-button history
  history.replaceState(null, "", s ? "#" + decodeURIComponent(s) : location.pathname + location.search);
}

function ensureDrawer() {
  if (drawer) return drawer;
  drawer = document.createElement("aside");
  drawer.id = "profileDrawer";
  drawer.hidden = true;
  drawer.innerHTML = `<header><h2 id="pfTitle">Property</h2>
    <button id="pfClose" title="Close">×</button></header>
    <div id="pfBody"></div>`;
  document.body.appendChild(drawer);
  drawer.querySelector("#pfClose").onclick = closeProfile;
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !drawer.hidden) closeProfile();
  });
  return drawer;
}

export function closeProfile() {
  if (drawer) drawer.hidden = true;
  setHashProperty(null);
}

function money(v) {
  return v === null || v === undefined ? "—" : fmtUSD.format(v);
}

function row(k, v) {
  return v === null || v === undefined || v === "" ? "" :
    `<span class="k">${k}</span><span>${v}</span>`;
}

function renderProfile(d) {
  const cur = d.current || {};
  const met = d.metrics || {};
  const addr = d.address || {};
  $("pfTitle").textContent = addr.label ? `${addr.label}${addr.city ? ", " + addr.city : ""}` : d.parcel_id;

  let h = "";
  if ((d.warnings || []).length) {
    h += `<div class="pf-warn">${d.warnings.map(esc).join("<br>")}</div>`;
  }
  h += `<section><h3>Overview</h3><div class="pp-grid">` +
    row("Parcel", esc(d.parcel_id)) +
    row("Owner", esc(cur.owner_display || "")) +
    row("Type", esc(cur.property_type || "")) +
    row("Year built", cur.year_built) +
    row("Stories", cur.stories) +
    row("Bldg area", cur.assessor_sqft ? fmt.format(cur.assessor_sqft) + " ft²" : null) +
    row("Land value", cur.land_value !== null ? money(cur.land_value) : null) +
    row("Impr. value", cur.improvement_value !== null ? money(cur.improvement_value) : null) +
    row("Total value", cur.total_value !== null ? money(cur.total_value) : null) +
    row("$ / sq ft", met.improvement_value_per_sqft) +
    row("Impr./land", met.improvement_to_land_ratio) +
    row("Tenure", met.ownership_tenure_years ? met.ownership_tenure_years + " yrs since last transfer" : null) +
    row("Legal", cur.legal && cur.legal.subdivision
      ? esc(`${cur.legal.subdivision}${cur.legal.lot ? " LOT " + cur.legal.lot : ""}${cur.legal.block && cur.legal.block !== "0" ? " BLK " + cur.legal.block : ""}`) : null) +
    `</div><div class="pf-asof">Assessor data as of ${esc(cur.as_of || "—")}</div></section>`;

  const blds = d.buildings || [];
  if (blds.length) {
    h += `<section><h3>Buildings (${blds.length})</h3><div class="pf-list">`;
    for (const b of blds.slice(0, 12)) {
      h += `<div class="pf-item">${b.is_primary ? "<b>Primary</b>" : "Accessory"}` +
        ` · ${esc(b.category || "?")}` +
        `${b.footprint_sqft ? " · " + fmt.format(Math.round(b.footprint_sqft)) + " ft² footprint" : ""}` +
        `<span class="pf-dim"> · match ${esc(b.match?.method || "none")}</span></div>`;
    }
    if (blds.length > 12) h += `<div class="pf-dim">+${blds.length - 12} more</div>`;
    h += `</div></section>`;
  }

  const tl = d.timeline || [];
  h += `<section><h3>Timeline (${tl.length})</h3><div class="pf-list">`;
  if (!tl.length) h += `<div class="pf-dim">No matched events yet — permits, recorded documents, and 311 requests appear here as sources are collected.</div>`;
  for (const e of tl.slice(0, 60)) {
    const label = EVENT_LABELS[e.event_type] || e.event_type;
    const low = e.match && e.match.confidence < 0.8;
    h += `<div class="pf-item${low ? " pf-low" : ""}"><span class="pf-date">${esc(e.event_at || "")}</span>` +
      ` <b>${esc(label)}</b> · ${esc(e.title || "")}` +
      `${e.amount ? " · " + money(e.amount) : ""}` +
      `${e.status ? " · " + esc(e.status) : ""}` +
      `${e.summary ? `<div class="pf-sub">${esc(e.summary)}</div>` : ""}` +
      `<div class="pf-src">${e.source?.url
        ? `<a href="${esc(e.source.url)}" target="_blank" rel="noopener">${esc(e.source.name || e.source.slug || "source")}</a>`
        : esc(e.source?.name || "")}` +
      `${e.source?.record_id ? " · #" + esc(e.source.record_id) : ""}` +
      `${low ? " · low-confidence match" : ""}</div></div>`;
  }
  if (tl.length > 60) h += `<div class="pf-dim">+${tl.length - 60} more</div>`;
  h += `</div></section>`;

  const srcs = d.sources || [];
  if (srcs.length) {
    h += `<section><h3>Sources</h3><div class="pf-list">`;
    for (const s of srcs) {
      h += `<div class="pf-item pf-dim">${s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.name)}</a>` : esc(s.name)}` +
        `${s.source_effective || s.last_ingest ? " · as of " + esc(s.source_effective || s.last_ingest) : ""}</div>`;
    }
    h += `</div></section>`;
  }
  h += `<div class="pf-foot">Unofficial compilation of public records · dates shown are event dates; observation may lag.</div>`;
  $("pfBody").innerHTML = h;
}

export function openProfile(key, { fly = false } = {}) {
  const api = profileApi();
  if (!api || !key) return false;
  ensureDrawer();
  drawer.hidden = false;
  $("pfTitle").textContent = "Property";
  $("pfBody").innerHTML = `<div class="pf-load"><span class="dh-spin"></span>Loading profile…</div>`;
  let p = cache.get(key);
  if (!p) {
    p = fetch(`${api}/v1/properties/${encodeURIComponent(key)}`)
      .then((r) => r.json())
      .catch(() => ({ error: "unreachable" }));
    cache.set(key, p);
  }
  p.then((d) => {
    if (drawer.hidden) return;
    if (!d || d.error) {
      cache.delete(key);
      $("pfBody").innerHTML = `<div class="pf-warn">Couldn't load this property's profile` +
        `${d && d.error === "property not found" ? " (parcel not in the database yet)" : ""}. ` +
        `<span class="pf-retry" id="pfRetry">Retry</span></div>`;
      const r = $("pfRetry");
      if (r) r.onclick = () => openProfile(key, { fly });
      return;
    }
    setHashProperty(d.property_id);
    renderProfile(d);
    if (fly && d.location && map) {
      const go = () => map.flyTo({ center: [d.location.lon, d.location.lat], zoom: 17, duration: 900 });
      if (map.loaded()) go(); else map.once("load", go);
    }
  });
  return true;
}

export function profileLinkHTML(parcelKey) {
  if (!profileApi() || !parcelKey) return "";
  return `<div class="pp-links pf-open-row"><span class="pf-open" data-profile-key="${esc(parcelKey)}">` +
    `View property profile →</span></div>`;
}

export function initProfile() {
  document.addEventListener("click", (e) => {
    const el = e.target && e.target.closest ? e.target.closest("[data-profile-key]") : null;
    if (el) openProfile(el.dataset.profileKey);
  });
  // services.json (which carries the API URL) loads in parallel with boot —
  // wait briefly for it before honoring a #property= deep link
  const wanted = hashParams().get("property");
  if (wanted) {
    const t0 = Date.now();
    const iv = setInterval(() => {
      if (profileApi()) {
        clearInterval(iv);
        openProfile(wanted, { fly: true });
      } else if (Date.now() - t0 > 6000) {
        clearInterval(iv);
      }
    }, 150);
  }
}
