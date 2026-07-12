/* Pulaski County Building Map — LR 311 service-request overlay + timelines.
   Data: pipeline/sr311_collect.py accumulating the city's CWI portal feed on
   the data branch (window is ~30 days at the source; the archive grows from
   2026-07 onward). Opened/closed dates are observed from status transitions,
   so older requests may carry only a last-updated date. */
import { $, fmt, esc, normAddrJS } from "../util.js";
import { map, refreshColors } from "../map.js";
import { state } from "../state.js";

const SR_BASE = "https://raw.githubusercontent.com/brandongrant/pulaski_building_map/data/sr311/out";
export const SR_CATS = {
  san: { label: "Sanitation", color: "#8bd3c7" },
  code: { label: "Code enforcement", color: "#e07a5f" },
  traffic: { label: "Traffic & lights", color: "#f3d54c" },
  street: { label: "Streets & drainage", color: "#6fa8dc" },
  animal: { label: "Animals", color: "#90be6d" },
  park: { label: "Parks", color: "#4ac16d" },
  tree: { label: "Trees", color: "#a0da39" },
  constr: { label: "Construction", color: "#f6a33b" },
  oth: { label: "Other", color: "#9aa3b5" },
};
const sr = {
  on: false,
  cats: new Set(Object.keys(SR_CATS)),
  openOnly: false,
  loaded: false,     // layers added
  loading: null,     // data promise
  data: null,
  byAddr: null,      // normAddrJS(addr) -> [props sorted newest first]
  statesApplied: false,
};

function srStats() {
  return fetch(SR_BASE + "/stats.json", { cache: "no-store" })
    .then((r) => (r.ok ? r.json() : null)).catch(() => null);
}

export function srDate(d) {
  const s = String(d || "");
  return s.length === 8 ? `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}` : s;
}

function srColor() {
  const m = ["match", ["get", "t"]];
  for (const [k, d] of Object.entries(SR_CATS)) m.push(k, d.color);
  m.push("#cfcfcf");
  return m;
}

function srFilter() {
  const f = ["all"];
  if (sr.cats.size < Object.keys(SR_CATS).length) {
    f.push(["in", ["get", "t"], ["literal", [...sr.cats]]]);
  }
  if (sr.openOnly) f.push(["==", ["get", "s"], "o"]);
  return f.length > 1 ? f : null;
}

function srDataLoad() {
  if (sr.loading) return sr.loading;
  sr.loading = Promise.all([
    srStats(),
    fetch(SR_BASE + "/requests.geojson", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null)).catch(() => null),
  ]).then(([s, g]) => {
    if (s) {
      const since = s.collecting_since ? ` since ${String(s.collecting_since).slice(0, 10)}` : "";
      $("srMeta").textContent = `· ${fmt.format(s.total_requests || 0)} requests${since}`;
    }
    if (!g || !Array.isArray(g.features)) {
      if (!s) $("srMeta").textContent = "· no data collected yet";
      sr.failed = true;
      return false;
    }
    sr.data = g;
    sr.byAddr = new Map();
    for (const f of g.features) {
      const p = f.properties || {};
      const key = normAddrJS(p.a);
      if (!key) continue;
      const rows = sr.byAddr.get(key);
      if (rows) rows.push(p); else sr.byAddr.set(key, [p]);
    }
    for (const rows of sr.byAddr.values()) rows.sort((a, b) => (b.u || 0) - (a.u || 0));
    return true;
  }).catch(() => {
    $("srMeta").textContent = "· data unavailable";
    sr.failed = true;
    return false;
  });
  return sr.loading;
}

async function srLoad() {
  if (sr.loaded) return true;
  const ok = await srDataLoad();
  if (!ok || !sr.data) return false;
  map.addSource("sr311", { type: "geojson", data: sr.data });
  map.addLayer({
    id: "sr-pts", type: "circle", source: "sr311", layout: { visibility: "none" },
    paint: {
      "circle-color": srColor(),
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 2, 13, 4.5, 16, 8],
      "circle-stroke-color": "#000000", "circle-stroke-width": 0.7,
      "circle-opacity": 0.9,
    },
  });
  if (map.getLayer("hit-ring")) map.moveLayer("hit-ring"); // keep search hits on top
  sr.loaded = true;
  return true;
}

function srRefresh() {
  if (!sr.loaded) return;
  map.setLayoutProperty("sr-pts", "visibility", sr.on ? "visible" : "none");
  map.setFilter("sr-pts", srFilter());
}

/* --------- "311 requests at address" color-by (feature-state join) ---------
   Buildings carry their address string in tiles from z13 up; the bld source
   promotes it to the feature id, so one setFeatureState per address colors
   every footprint sharing that address. Below z13 buildings stay in the
   unknown color — the overlay points are the zoomed-out view. */
export function sr311EnsureStates() {
  srDataLoad().then((ok) => {
    if (!ok || sr.statesApplied) return;
    const counts = new Map();
    for (const f of sr.data.features) {
      const a = f.properties && f.properties.a;
      if (a) counts.set(a, (counts.get(a) || 0) + 1);
    }
    const apply = () => {
      for (const [a, n] of counts) {
        map.setFeatureState({ source: "bld", sourceLayer: "buildings", id: a }, { sr: n });
      }
      sr.statesApplied = true;
      if (state.attr === "sr311") refreshColors();
    };
    if (map.getSource("bld")) apply(); else map.once("load", apply);
  });
}

export function sr311Timeline(bldProps) {
  if (!bldProps.addr || sr.failed) return "";
  if (!sr.byAddr) {
    return `<div class="tt-veh">311 request index is loading; click again for request history.</div>`;
  }
  const rows = sr.byAddr.get(normAddrJS(bldProps.addr));
  if (!rows || !rows.length) {
    return `<div class="tt-veh">No LR 311 requests collected at this address.</div>`;
  }
  const items = [];
  for (const p of rows) {
    // lead with the opened date; when only the closure is known, the closed
    // tail already carries the row's one date
    const when = p.o ? srDate(p.o) : (p.cl ? "" : srDate(p.u));
    const tail = p.cl ? `closed ${srDate(p.cl)}` : esc(p.sd || "");
    items.push(`<div>${when ? when + " · " : ""}${esc(p.ty)}${tail ? " · " + tail : ""}</div>`);
    if (items.length >= 8) break;
  }
  const more = rows.length > items.length ? `<div>+${rows.length - items.length} more</div>` : "";
  return `<div class="tt-veh pm-tl"><b>311 requests at this address</b>${items.join("")}${more}</div>`;
}

export function initRequests311() {
  const chips = $("srCats");
  for (const [k, d] of Object.entries(SR_CATS)) {
    const el = document.createElement("div");
    el.className = "chip on";
    el.textContent = d.label;
    el.style.background = d.color;
    el.style.borderColor = d.color;
    el.onclick = () => {
      if (sr.cats.has(k)) {
        sr.cats.delete(k);
        el.classList.remove("on");
        el.style.background = "var(--ctl-bg)";
        el.style.borderColor = "var(--ctl-border)";
      } else {
        sr.cats.add(k);
        el.classList.add("on");
        el.style.background = d.color;
        el.style.borderColor = d.color;
      }
      srRefresh();
    };
    chips.appendChild(el);
  }
  $("srOn").onchange = async () => {
    sr.on = $("srOn").checked;
    $("srControls").hidden = !sr.on;
    if (sr.on) await srLoad();
    srRefresh();
  };
  $("srOpenOnly").onchange = () => {
    sr.openOnly = $("srOpenOnly").checked;
    srRefresh();
  };
  srStats().then((s) => {
    if (s && s.total_requests) {
      const since = s.collecting_since ? ` since ${String(s.collecting_since).slice(0, 10)}` : "";
      $("srMeta").textContent = `· ${fmt.format(s.total_requests)} requests${since}`;
    }
  });
  // warm the index so building popups can show request history without the
  // overlay being switched on (same pattern as the deeds timeline)
  setTimeout(srDataLoad, 3000);
}
