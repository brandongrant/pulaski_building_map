/* Pulaski County Building Map — LR permits overlay + building permit timeline */
import { $, fmt, fmtUSD, esc, normAddrJS } from "../util.js";
import { map } from "../map.js";

export const PM_CATS = {
  new: { label: "New construction", color: "#4ac16d" },
  add: { label: "Addition", color: "#a0da39" },
  rem: { label: "Remodel/repair", color: "#f3d54c" },
  demo: { label: "Demolition", color: "#ff4d4d" },
  usv: { label: "Unsafe/vacant", color: "#e07a5f" },
  roof: { label: "Roofing", color: "#f6a33b" },
  ele: { label: "Electrical", color: "#6fa8dc" },
  mec: { label: "Mechanical", color: "#8bd3c7" },
  plu: { label: "Plumbing", color: "#b39ddb" },
  sign: { label: "Sign/banner", color: "#cfcfcf" },
  oth: { label: "Other", color: "#9aa3b5" },
};
const PM_VALS = [0, 1000, 5000, 10000, 50000, 100000, 500000];
const pm = { on: false, lo: 2019, hi: 2026, cats: new Set(Object.keys(PM_CATS)),
             minv: 0, loaded: false };

function pmFilter() {
  const f = ["all",
    [">=", ["get", "d"], pm.lo * 10000 + 101],
    ["<=", ["get", "d"], pm.hi * 10000 + 1231]];
  if (pm.cats.size < Object.keys(PM_CATS).length) {
    f.push(["in", ["get", "t"], ["literal", [...pm.cats]]]);
  }
  if (pm.minv > 0) f.push([">=", ["coalesce", ["get", "v"], 0], pm.minv]);
  return f;
}

async function pmLoad() {
  if (pm.loaded) return true;
  try {
    const meta = await fetch("data/permits/permits_meta.json").then((r) => {
      if (!r.ok) throw 0;
      return r.json();
    });
    $("pmMeta").textContent = `· ${fmt.format(meta.count)} permits`;
    map.addSource("pm", { type: "geojson", data: "data/permits/permits.geojson" });
    const match = ["match", ["get", "t"]];
    for (const [k, d] of Object.entries(PM_CATS)) match.push(k, d.color);
    match.push("#9aa3b5");
    map.addLayer({
      id: "pm-pts", type: "circle", source: "pm", layout: { visibility: "none" },
      paint: {
        "circle-color": match,
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 2, 13, 4.5, 16, 8],
        "circle-stroke-color": "#000000", "circle-stroke-width": 0.7,
        "circle-opacity": 0.9,
      },
    });
    if (map.getLayer("hit-ring")) map.moveLayer("hit-ring"); // keep search hits on top
    pm.loaded = true;
    return true;
  } catch (e) {
    $("pmMeta").textContent = "· data unavailable";
    return false;
  }
}

function pmRefresh() {
  if (!pm.loaded) return;
  map.setLayoutProperty("pm-pts", "visibility", pm.on ? "visible" : "none");
  map.setFilter("pm-pts", pmFilter());
}

export function permitTimeline(bldProps) {
  if (!bldProps.addr) return "";
  if (!pm.loaded || !map.getSource("pm")) {
    return `<div class="tt-veh">Enable the permit overlay to load this building's permit history.</div>`;
  }
  const key = normAddrJS(bldProps.addr);
  const fs = map.querySourceFeatures("pm").filter((f) => f.properties.a === key);
  if (!fs.length) return `<div class="tt-veh">No LR permits on record at this address (2019+).</div>`;
  fs.sort((x, y) => y.properties.d - x.properties.d);
  const seen = new Set();
  const items = [];
  for (const f of fs) {
    const p = f.properties;
    if (seen.has(p.n)) continue;
    seen.add(p.n);
    const d = String(p.d);
    items.push(`<div>${d.slice(0, 4)}-${d.slice(4, 6)} · ${PM_CATS[p.t].label}` +
               `${p.v ? " · " + fmtUSD.format(p.v) : ""}` +
               `${p.ds ? " — " + esc(p.ds) : ""}</div>`);
    if (items.length >= 10) break;
  }
  return `<div class="tt-veh pm-tl"><b>Permit history (LR)</b>${items.join("")}</div>`;
}

export function initPermits() {
  const chips = $("pmCats");
  for (const [k, d] of Object.entries(PM_CATS)) {
    const el = document.createElement("div");
    el.className = "chip on";
    el.textContent = d.label;
    el.style.background = d.color;
    el.style.borderColor = d.color;
    el.onclick = () => {
      if (pm.cats.has(k)) {
        pm.cats.delete(k);
        el.classList.remove("on");
        el.style.background = "var(--ctl-bg)";
        el.style.borderColor = "var(--ctl-border)";
      } else {
        pm.cats.add(k);
        el.classList.add("on");
        el.style.background = d.color;
        el.style.borderColor = d.color;
      }
      pmRefresh();
    };
    chips.appendChild(el);
  }
  const lo = $("pmLo"), hi = $("pmHi");
  const showYr = () => { $("pmYrShow").textContent = `${pm.lo} – ${pm.hi}`; };
  showYr();
  const onYr = (ev) => {
    let a = Number(lo.value), b = Number(hi.value);
    if (a > b) { if (ev.target === lo) b = a; else a = b; lo.value = a; hi.value = b; }
    pm.lo = a; pm.hi = b;
    showYr();
    pmRefresh();
  };
  lo.oninput = onYr;
  hi.oninput = onYr;
  $("pmVal").oninput = () => {
    pm.minv = PM_VALS[Number($("pmVal").value)];
    $("pmValV").textContent = pm.minv ? "≥ " + fmtUSD.format(pm.minv) : "any";
    pmRefresh();
  };
  $("pmOn").onchange = async () => {
    pm.on = $("pmOn").checked;
    $("pmControls").hidden = !pm.on;
    if (pm.on) await pmLoad();
    pmRefresh();
  };
  fetch("data/permits/permits_meta.json").then((r) => (r.ok ? r.json() : null))
    .then((m) => { if (m) $("pmMeta").textContent = `· ${fmt.format(m.count)} permits 2019–${String(m.date_max).slice(0, 4)}`; })
    .catch(() => {});
}
