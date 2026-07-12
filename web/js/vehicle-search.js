/* Pulaski County Building Map — vehicle search (assessor personal property) */
// Searchable index of assessor personal-property vehicles, each pinned to its
// building's representative point. Built by pipeline/build_vehicle_index.py from
// the per-building `veh` strings (≤6 vehicles per address). The browser filters
// the flat table client-side and renders matches as a clustered point layer.
import { $, fmt, esc, titleCase } from "./util.js";
import { map } from "./map.js";
import { ui } from "./state.js";

const VEH_COLOR = "#ff2ea6";
const VEH_MAX_PLOT = 6000;      // cap plotted locations so broad searches stay smooth
const VEH_LIST_MAX = 120;       // rows in the panel results list
const veh = { idx: null, loading: null };

function vehLoad() {
  if (veh.idx) return Promise.resolve(veh.idx);
  if (veh.loading) return veh.loading;
  $("vehMeta").textContent = "· loading…";
  veh.loading = fetch("data/vehicles.json")
    .then((r) => { if (!r.ok) throw new Error("vehicles.json unavailable"); return r.json(); })
    .then((d) => {
      d._makeLc = d.makes.map((m) => m.toLowerCase());
      d._modelLc = d.models.map((m) => m.toLowerCase());
      const dl = $("vehMakeList");
      (d.make_order || d.makes.map((_, i) => i)).slice(0, 400).forEach((i) => {
        const o = document.createElement("option");
        o.value = titleCase(d.makes[i]);
        dl.appendChild(o);
      });
      $("vehMeta").textContent = `· ${fmt.format(d.stats.vehicles)} on file`;
      veh.idx = d;
      return d;
    })
    .catch((e) => {
      // clear the memo so a later Search/focus can retry after a transient failure
      veh.loading = null;
      $("vehMeta").textContent = "· unavailable";
      throw e;
    });
  return veh.loading;
}

function vehEnsureLayers() {
  if (map.getSource("veh-src")) return;
  map.addSource("veh-src", {
    type: "geojson", data: { type: "FeatureCollection", features: [] },
    cluster: true, clusterRadius: 44, clusterMaxZoom: 14,
  });
  map.addLayer({
    id: "veh-cluster", type: "circle", source: "veh-src", filter: ["has", "point_count"],
    paint: {
      "circle-color": ["step", ["get", "point_count"], "#ff8ad0", 10, "#ff4fb5", 50, VEH_COLOR, 250, "#e01690"],
      "circle-opacity": 0.85,
      "circle-radius": ["interpolate", ["linear"], ["get", "point_count"], 2, 12, 25, 19, 250, 28],
      "circle-stroke-color": "#ffffff", "circle-stroke-width": 1.2,
    },
  });
  map.addLayer({
    id: "veh-point", type: "circle", source: "veh-src", filter: ["!", ["has", "point_count"]],
    paint: {
      "circle-color": VEH_COLOR,
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 4, 14, 7, 17, 10],
      "circle-stroke-color": "#ffffff", "circle-stroke-width": 1.3, "circle-opacity": 0.95,
    },
  });
}

function vehSetVisible(on) {
  for (const l of ["veh-cluster", "veh-point"]) {
    if (map.getLayer(l)) map.setLayoutProperty(l, "visibility", on ? "visible" : "none");
  }
}

export function vehRun() {
  vehLoad().then((d) => {
    const mk = $("vehMake").value.trim().toLowerCase();
    const md = $("vehModel").value.trim().toLowerCase();
    let ylo = parseInt($("vehYrLo").value, 10);
    let yhi = parseInt($("vehYrHi").value, 10);
    // a single year field fills both bounds; both empty means no year filter
    if (isFinite(ylo) && !isFinite(yhi)) yhi = ylo;
    if (isFinite(yhi) && !isFinite(ylo)) ylo = yhi;
    const hasYr = isFinite(ylo) && isFinite(yhi);
    if (hasYr && ylo > yhi) { const t = ylo; ylo = yhi; yhi = t; }
    if (!mk && !md && !hasYr) { vehClear(); return; }

    // precompute make/model match masks (indexed by intern id)
    const mkMask = mk ? d._makeLc.map((s) => s.includes(mk)) : null;
    const mdMask = md ? d._modelLc.map((s) => s.includes(md)) : null;

    const byLoc = new Map();  // locIdx -> { n, labels[] }
    let total = 0;
    for (let i = 0; i < d.veh.length; i++) {
      const v = d.veh[i];
      const yr = v[1];
      if (mkMask && !mkMask[v[2]]) continue;
      if (mdMask && !mdMask[v[3]]) continue;
      if (hasYr && (yr < ylo || yr > yhi)) continue;
      total++;
      let e = byLoc.get(v[0]);
      if (!e) { e = { n: 0, labels: [] }; byLoc.set(v[0], e); }
      e.n++;
      if (e.labels.length < 12) {
        e.labels.push((yr ? yr + " " : "") + d.makes[v[2]] + " " + d.models[v[3]]);
      }
    }
    vehRender(d, byLoc, total);
  }).catch(() => {
    $("vehResults").hidden = false;
    $("vehResults").innerHTML = `<div class="veh-sum">Vehicle index could not be loaded.</div>`;
  });
}

function vehRender(d, byLoc, total) {
  const entries = [...byLoc.entries()].sort((a, b) => b[1].n - a[1].n);
  vehEnsureLayers();

  const plot = entries.slice(0, VEH_MAX_PLOT);
  const features = plot.map(([li, e]) => {
    const L = d.loc[li];
    return {
      type: "Feature",
      geometry: { type: "Point", coordinates: [L[0], L[1]] },
      properties: { a: L[2] || "", c: d.cities[L[3]] || "", n: e.n, v: e.labels.join("\n") },
    };
  });
  map.getSource("veh-src").setData({ type: "FeatureCollection", features });
  vehSetVisible(true);
  // an overlay enabled after this search would be added on top; keep the result
  // pins topmost so their click priority matches what the user sees
  for (const l of ["veh-cluster", "veh-point"]) if (map.getLayer(l)) map.moveLayer(l);

  const rs = $("vehResults");
  rs.hidden = false;
  if (!total) {
    rs.innerHTML = `<div class="veh-sum">No matching vehicles found.</div>`;
    return;
  }
  const capped = byLoc.size > VEH_MAX_PLOT ? ` · mapping the ${fmt.format(VEH_MAX_PLOT)} densest` : "";
  let html = `<div class="veh-sum"><b>${fmt.format(total)}</b> vehicle${total > 1 ? "s" : ""} · ` +
    `<b>${fmt.format(byLoc.size)}</b> location${byLoc.size > 1 ? "s" : ""}${capped}</div><div class="veh-list">`;
  for (const [li, e] of entries.slice(0, VEH_LIST_MAX)) {
    const L = d.loc[li];
    html += `<div class="veh-item" data-lon="${L[0]}" data-lat="${L[1]}">` +
      `<span class="veh-a">${esc(L[2] || "(no address)")}</span>` +
      `<span class="veh-n">${e.n}</span></div>`;
  }
  html += `</div>`;
  rs.innerHTML = html;
  rs.querySelectorAll(".veh-item").forEach((el) => {
    el.onclick = () => {
      // on phones the drawer covers the map centre — tuck it away so the flyTo
      // target isn't hidden behind it
      if (window.matchMedia("(max-width: 640px)").matches && ui.setPanel) ui.setPanel(true);
      map.flyTo({ center: [Number(el.dataset.lon), Number(el.dataset.lat)], zoom: 17, duration: 900 });
    };
  });

  // frame the results; guard the fitBounds -Infinity crash on tiny/hidden maps
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const f of features) {
    const [x, y] = f.geometry.coordinates;
    if (x < minX) minX = x; if (y < minY) minY = y;
    if (x > maxX) maxX = x; if (y > maxY) maxY = y;
  }
  if (features.length) {
    const bounds = [[minX, minY], [maxX, maxY]];
    try { map.fitBounds(bounds, { padding: 60, maxZoom: 16, duration: 800 }); }
    catch (e) { try { map.fitBounds(bounds, { duration: 0 }); } catch (_) { /* ignore */ } }
  }
}

function vehClear() {
  $("vehMake").value = "";
  $("vehModel").value = "";
  $("vehYrLo").value = "";
  $("vehYrHi").value = "";
  $("vehResults").hidden = true;
  $("vehResults").innerHTML = "";
  if (map.getSource("veh-src")) map.getSource("veh-src").setData({ type: "FeatureCollection", features: [] });
  vehSetVisible(false);
}

export function initVehicleSearch() {
  const ids = ["vehMake", "vehModel", "vehYrLo", "vehYrHi"];
  // warm the index the moment the user engages the form
  ids.forEach((id) => $(id).addEventListener("focus", () => { vehLoad().catch(() => {}); }, { once: true }));
  ids.forEach((id) => $(id).addEventListener("keydown", (e) => { if (e.key === "Enter") vehRun(); }));
  $("vehSearch").onclick = vehRun;
  $("vehClear").onclick = vehClear;
}
