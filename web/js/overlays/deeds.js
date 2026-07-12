/* Pulaski County Building Map — recent deed-activity overlay + address timeline */
import { $, fmt, esc, normAddrJS } from "../util.js";
import { map } from "../map.js";
import { deedDocLink } from "../api.js";

const DEED_BASE = "https://raw.githubusercontent.com/brandongrant/pulaski_building_map/data/deeds/out";
export const DEED_TYPES = {
  WAD: { label: "Warranty deed", color: "#4ac16d" },
  QCD: { label: "Quit claim", color: "#f3d54c" },
  BFD: { label: "Beneficiary deed", color: "#6fa8dc" },
  OTD: { label: "Other deed", color: "#b39ddb" },
};
const deed = {
  on: false,
  types: new Set(Object.keys(DEED_TYPES)),
  loaded: false,
  loading: null,
  stats: null,
  data: null,
  byAddr: null,
};

function deedStats() {
  return fetch(DEED_BASE + "/stats.json", { cache: "no-store" })
    .then((r) => (r.ok ? r.json() : null)).catch(() => null);
}

function deedColor() {
  const m = ["match", ["get", "t"]];
  for (const [k, d] of Object.entries(DEED_TYPES)) m.push(k, d.color);
  m.push("#cfcfcf");
  return m;
}

function deedFilter() {
  return deed.types.size === Object.keys(DEED_TYPES).length
    ? null : ["in", ["get", "t"], ["literal", [...deed.types]]];
}

function deedMetaText(s) {
  if (!s) return "· no data collected yet";
  const earliest = s.earliest ? String(s.earliest) : "";
  return `· ${fmt.format(s.total_documents || 0)} docs` +
    (earliest ? ` since ${earliest.slice(0, 4)}-${earliest.slice(4, 6)}-${earliest.slice(6, 8)}` : "");
}

function deedLabel(p) {
  return p.dt || (DEED_TYPES[p.t] && DEED_TYPES[p.t].label) || "Recorded document";
}

function partyShort(s) {
  return String(s || "").split(";")[0].trim();
}

async function deedDataLoad() {
  if (deed.loading) return deed.loading;
  deed.loading = Promise.all([
    deedStats(),
    fetch(DEED_BASE + "/recent_activity.geojson", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null)).catch(() => null),
  ]).then(([s, g]) => {
    deed.stats = s;
    $("deedMeta").textContent = deedMetaText(s);
    if (!g || !Array.isArray(g.features)) return false;
    deed.data = g;
    deed.byAddr = new Map();
    for (const f of g.features) {
      const p = f.properties || {};
      const key = normAddrJS(p.a);
      if (!key) continue;
      const rows = deed.byAddr.get(key);
      if (rows) rows.push(p); else deed.byAddr.set(key, [p]);
    }
    for (const rows of deed.byAddr.values()) rows.sort((a, b) => (b.d || 0) - (a.d || 0));
    return true;
  }).catch(() => {
    $("deedMeta").textContent = "· data unavailable";
    return false;
  });
  return deed.loading;
}

async function deedLoad() {
  if (deed.loaded) return true;
  const ok = await deedDataLoad();
  if (!ok || !deed.data) return false;
  map.addSource("deed", { type: "geojson", data: deed.data });
  map.addLayer({
    id: "deed-pts", type: "circle", source: "deed", layout: { visibility: "none" },
    paint: {
      "circle-color": deedColor(),
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 2, 13, 4.5, 16, 7.5],
      "circle-stroke-color": "#000000", "circle-stroke-width": 0.8,
      "circle-opacity": 0.88,
    },
  });
  if (map.getLayer("hit-ring")) map.moveLayer("hit-ring"); // keep search hits on top
  deed.loaded = true;
  return true;
}

function deedRefresh() {
  if (!deed.loaded) return;
  map.setLayoutProperty("deed-pts", "visibility", deed.on ? "visible" : "none");
  map.setFilter("deed-pts", deedFilter());
}

export function initDeeds() {
  const chips = $("deedTypes");
  for (const [k, d] of Object.entries(DEED_TYPES)) {
    const el = document.createElement("div");
    el.className = "chip on";
    el.textContent = d.label;
    el.style.background = d.color;
    el.style.borderColor = d.color;
    el.onclick = () => {
      if (deed.types.has(k)) {
        deed.types.delete(k);
        el.classList.remove("on");
        el.style.background = "var(--ctl-bg)";
        el.style.borderColor = "var(--ctl-border)";
      } else {
        deed.types.add(k);
        el.classList.add("on");
        el.style.background = d.color;
        el.style.borderColor = d.color;
      }
      deedRefresh();
    };
    chips.appendChild(el);
  }
  $("deedOn").onchange = async () => {
    deed.on = $("deedOn").checked;
    $("deedControls").hidden = !deed.on;
    if (deed.on) await deedLoad();
    deedRefresh();
  };
  deedStats().then((s) => {
    if (s && s.total_documents) {
      $("deedMeta").textContent = `· ${fmt.format(s.total_documents)} docs`;
    }
  });
  setTimeout(deedDataLoad, 2500);
}

export function deedsForBuilding(bldProps) {
  if (!bldProps.addr || !deed.byAddr) return null;
  return deed.byAddr.get(normAddrJS(bldProps.addr)) || [];
}

export function deedsTimeline(bldProps, docs = undefined) {
  if (!bldProps.addr) return "";
  if (docs === undefined) docs = deedsForBuilding(bldProps);
  if (docs === null) {
    return `<div class="tt-veh">Recent recorded-document index is loading; click again for deed history.</div>`;
  }
  if (!docs || !docs.length) {
    return `<div class="tt-veh">No recent recorded deeds matched at this address.</div>`;
  }
  const items = [];
  for (const p of docs) {
    const d = String(p.d || "");
    const left = partyShort(p.g1);
    const right = partyShort(p.g2);
    items.push(`<div>${d.slice(0, 4)}-${d.slice(4, 6)} · ${esc(deedLabel(p))}` +
      `${left || right ? " · " + esc(left || "?") + " → " + esc(right || "?") : ""}` +
      `${p.n ? " · " + deedDocLink(p.n, "#" + p.n) : ""}</div>`);
    if (items.length >= 8) break;
  }
  return `<div class="tt-veh pm-tl"><b>Recent recorded documents</b>${items.join("")}` +
    `<div>Document links open PulaskiDeeds details and image pages.</div></div>`;
}
