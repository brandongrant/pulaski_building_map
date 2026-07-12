/* Pulaski County Building Map — MapLibre map, color/filter expressions, legend */
import { $, fmt } from "./util.js";
import { cfg, ATTRS, CATS, PALETTES, UNKNOWN_COLOR } from "./config.js";
import { state, ui } from "./state.js";

export let map = null;

/* ------------------------------------------------- color expressions */
function currentColors() {
  const c = PALETTES[state.ramp].colors.slice();
  return state.reverse ? c.reverse() : c;
}

function stopPositions(a, n) {
  const [lo, hi] = a.domain;
  const out = [];
  for (let i = 0; i < n; i++) {
    const t = i / (n - 1);
    out.push(a.scale === "log" ? lo * Math.pow(hi / lo, t) : lo + t * (hi - lo));
  }
  // ensure strictly ascending
  for (let i = 1; i < out.length; i++) if (out[i] <= out[i - 1]) out[i] = out[i - 1] + 1e-6;
  return out;
}

function colorExpr() {
  const a = ATTRS[state.attr];
  if (a.type === "cat") {
    const m = ["match", ["coalesce", ["get", "cat"], 0]];
    for (const k of Object.keys(CATS)) m.push(Number(k), CATS[k].color);
    m.push(UNKNOWN_COLOR);
    return m;
  }
  const colors = currentColors();
  const stops = stopPositions(a, colors.length);
  const v = a.valueExpr || ["coalesce", ["get", state.attr], 0];
  const interp = ["interpolate", ["linear"], v];
  stops.forEach((s, i) => interp.push(s, colors[i]));
  const unknown = a.unknownExpr || ["<=", v, state.attr === "st" ? 0.01 : 0];
  return ["case", unknown, UNKNOWN_COLOR, interp];
}

/* ------------------------------------------------- filter expression */
function filterExpr() {
  const f = ["all"];
  if (state.yrLo > cfg.year.min || state.yrHi < cfg.year.max || !state.showUnk) {
    const v = ["coalesce", ["get", "yr"], 0];
    const inRange = ["all", [">=", v, state.yrLo], ["<=", v, state.yrHi]];
    f.push(state.showUnk ? ["any", ["<=", v, 0], inRange] : ["all", [">", v, 0], inRange]);
  }
  if (state.cats.size < 8) f.push(["in", ["coalesce", ["get", "cat"], 0], ["literal", [...state.cats]]]);
  if (state.mainOnly) f.push(["==", ["coalesce", ["get", "main"], 1], 1]);
  return f.length > 1 ? f : true;
}

/* ------------------------------------------------- map */
export function initMap(onReady) {
  const protocol = new pmtiles.Protocol();
  maplibregl.addProtocol("pmtiles", protocol.tile);
  const pmUrl = new URL("data/buildings.pmtiles", location.href).href;

  map = new maplibregl.Map({
    container: "map",
    hash: true,
    attributionControl: false,
    style: {
      version: 8,
      sources: {},
      layers: [{ id: "bg", type: "background", paint: { "background-color": state.bg } }],
    },
    center: cfg.center,
    zoom: 10,
    // keep the floor above the tileset's minzoom (z8) — below a source's
    // minzoom MapLibre loads nothing and the screen goes black
    minZoom: Math.max(8, cfg.minzoom),
    maxZoom: 19,
    maxPitch: 70,
  });

  map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-right");
  map.addControl(new maplibregl.ScaleControl({ unit: "imperial" }), "bottom-left");
  map.addControl(new maplibregl.AttributionControl({
    compact: true,
    customAttribution: "Footprints © PAgis · Attributes © Pulaski County Assessor",
  }), "bottom-right");

  // phones: touching or panning the map slides the drawer out of the way
  const autoHidePanel = () => {
    if (window.matchMedia("(max-width: 640px)").matches && ui.setPanel) ui.setPanel(true);
  };
  map.on("click", autoHidePanel);
  map.on("dragstart", autoHidePanel);

  map.on("load", () => {
    // promote the tile address string to the feature id so address-keyed
    // feature-state joins (311 request counts) color every footprint sharing
    // an address; tiles only carry addr from z13 up, lower zooms get no id
    map.addSource("bld", { type: "vector", url: "pmtiles://" + pmUrl, promoteId: "addr" });

    map.addLayer({
      id: "bld-fill", type: "fill", source: "bld", "source-layer": "buildings",
      paint: { "fill-color": colorExpr(), "fill-opacity": state.opacity, "fill-antialias": true },
    });
    map.addLayer({
      id: "bld-line", type: "line", source: "bld", "source-layer": "buildings", minzoom: 13.5,
      paint: {
        "line-color": "#000000",
        "line-width": 0.6,
        "line-opacity": ["interpolate", ["linear"], ["zoom"], 13.5, 0, 15.5, 0.4],
      },
    });
    map.addLayer({
      id: "bld-3d", type: "fill-extrusion", source: "bld", "source-layer": "buildings",
      layout: { visibility: "none" },
      paint: {
        "fill-extrusion-color": colorExpr(),
        "fill-extrusion-height": heightExpr(),
        "fill-extrusion-opacity": 0.95,
      },
    });

    // search-hit markers (owner/address search results) — kept above overlays
    map.addSource("hits", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    map.addLayer({
      id: "hit-ring", type: "circle", source: "hits",
      paint: {
        "circle-color": "rgba(243,213,76,0.18)",
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 4, 13, 9, 16, 14],
        "circle-stroke-color": "#f3d54c",
        "circle-stroke-width": 2,
      },
    });

    if (!location.hash || location.hash.length < 4) {
      map.fitBounds([[cfg.bounds[0], cfg.bounds[1]], [cfg.bounds[2], cfg.bounds[3]]], { padding: 30, duration: 0 });
    }
    if (onReady) onReady();
    map.once("idle", () => $("loading").classList.add("done"));
  });

  map.on("error", (e) => {
    const msg = (e && e.error && e.error.message) || "";
    if (/pmtiles|buildings/.test(msg)) {
      $("loading").innerHTML = "⚠ Could not load tiles: " + msg;
      $("loading").classList.remove("done");
    }
  });
}

export function heightExpr() {
  const perStory = 3.5 * (state.hMult / 5);
  return ["*", ["max", ["coalesce", ["get", "st"], 1], 1], perStory];
}

export function refreshColors() {
  map.setPaintProperty("bld-fill", "fill-color", colorExpr());
  map.setPaintProperty("bld-3d", "fill-extrusion-color", colorExpr());
  renderLegend();
}

export function refreshFilter() {
  const f = filterExpr();
  for (const l of ["bld-fill", "bld-line", "bld-3d"]) map.setFilter(l, f === true ? null : f);
}

/* ------------------------------------------------- legend */
export function renderLegend() {
  const a = ATTRS[state.attr];
  const catBox = $("catLegend");
  const grad = $("legendGrad");
  const labels = $("legendLabels");
  const hist = $("hist");

  if (a.type === "cat") {
    grad.style.display = "none";
    labels.style.display = "none";
    hist.style.display = "none";
    $("rampRow").style.display = "none";
    catBox.style.display = "flex";
    catBox.innerHTML = "";
    const counts = cfg.cats || {};
    for (const k of [1, 5, 6, 3, 2, 4, 7, 0]) {
      const c = CATS[k];
      const n = counts[String(k)] || 0;
      const div = document.createElement("div");
      div.className = "ci";
      div.innerHTML = `<span class="sw" style="background:${c.color}"></span>${c.label}<span class="n">${fmt.format(n)}</span>`;
      catBox.appendChild(div);
    }
    return;
  }

  $("rampRow").style.display = "";
  catBox.style.display = "none";
  grad.style.display = "block";
  labels.style.display = "flex";

  const colors = currentColors();
  const g = grad.getContext("2d");
  const gr = g.createLinearGradient(0, 0, grad.width, 0);
  colors.forEach((c, i) => gr.addColorStop(i / (colors.length - 1), c));
  g.fillStyle = gr;
  g.fillRect(0, 0, grad.width, grad.height);

  const [lo, hi] = a.domain;
  const mid = a.scale === "log" ? Math.sqrt(lo * hi) : (lo + hi) / 2;
  const hiLbl = String(a.fmtV(hi));
  labels.innerHTML = `<span>${state.attr === "yr" ? "≤" : ""}${a.fmtV(lo)}</span><span>${a.fmtV(mid)}</span><span>${hiLbl}${state.attr === "yr" || hiLbl.endsWith("+") ? "" : "+"}</span>`;

  // decade histogram only for year
  if (state.attr === "yr" && cfg.decades) {
    hist.style.display = "block";
    const h = hist.getContext("2d");
    h.clearRect(0, 0, hist.width, hist.height);
    const entries = Object.entries(cfg.decades).map(([d, n]) => [Number(d), n]).sort((x, y) => x[0] - y[0]);
    const maxN = Math.max(...entries.map((e) => Math.sqrt(e[1])));
    const bw = hist.width / entries.length;
    entries.forEach(([dec, n], i) => {
      const t = Math.min(1, Math.max(0, (dec + 5 - lo) / (hi - lo)));
      const ci = t * (colors.length - 1);
      const c0 = colors[Math.floor(ci)];
      h.fillStyle = c0;
      const bh = Math.max(1.5, (Math.sqrt(n) / maxN) * (hist.height - 14));
      h.fillRect(i * bw + 1, hist.height - 12 - bh, bw - 2, bh);
    });
    h.fillStyle = "#9aa3b5";
    h.font = "9.5px sans-serif";
    h.textAlign = "left";
    h.fillText(String(entries[0][0]) + "s", 1, hist.height - 2);
    h.textAlign = "right";
    h.fillText(String(entries[entries.length - 1][0]) + "s", hist.width - 1, hist.height - 2);
  } else {
    hist.style.display = "none";
  }
}
