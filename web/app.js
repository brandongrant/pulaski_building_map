/* Pulaski County Building Map */
"use strict";

const $ = (id) => document.getElementById(id);
const fmt = new Intl.NumberFormat("en-US");
const fmtUSD = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });

const UNKNOWN_COLOR = "#39404f";

const CATS = {
  0: { label: "Unknown", color: "#39404f" },
  1: { label: "Single-family", color: "#f4c95d" },
  2: { label: "Condo (HPR)", color: "#ef7bd0" },
  3: { label: "Duplex / Tri / Quad", color: "#ff8a4a" },
  4: { label: "Mobile home", color: "#7fd069" },
  5: { label: "Commercial / Apts", color: "#4aa8ff" },
  6: { label: "Exempt / Public", color: "#b9a3e3" },
  7: { label: "Outbuilding", color: "#5e6b7f" },
};

/* palettes: first color = oldest / lowest value */
const PALETTES = {
  london: { label: "Colouring London", colors: ["#4daf9c", "#53a7c9", "#3d7fb0", "#3b5ba8", "#5d3b8c", "#8e1e5f", "#b31c1c", "#d92120", "#e75323", "#ef7b28", "#f6a33b", "#f3d54c", "#f7f1a1"] },
  amsterdam: { label: "Amsterdam fire", colors: ["#70040b", "#a81605", "#d24e0f", "#ee8f1e", "#f8c53c", "#f2e29b", "#bfe0e8", "#7fc0e0", "#4292c6", "#2166ac"] },
  viridis: { label: "Viridis", colors: ["#440154", "#46327e", "#365c8d", "#277f8e", "#1fa187", "#4ac16d", "#a0da39", "#fde725"] },
  magma: { label: "Magma", colors: ["#0a0722", "#1d1147", "#51127c", "#822681", "#b63679", "#e65164", "#fb8861", "#fec287", "#fcfdbf"] },
  turbo: { label: "Turbo", colors: ["#30123b", "#4145ab", "#4675ed", "#39a2fc", "#1bcfd4", "#24eca6", "#61fc6c", "#a4fc3b", "#d1e834", "#f3c63a", "#fe9b2d", "#f36315", "#d93806", "#b11901", "#7a0402"] },
  cividis: { label: "Cividis", colors: ["#00204d", "#00336f", "#39486b", "#575d6d", "#707173", "#8a8779", "#a69d75", "#c4b56c", "#e4cf5b", "#ffea46"] },
  coolwarm: { label: "Cool–Warm", colors: ["#3b4cc0", "#6688ee", "#88abfd", "#b8d0f9", "#dddddd", "#f5c4ac", "#f39475", "#dd604d", "#b40426"] },
};

const BG_COLORS = ["#05060a", "#0b0e17", "#10131f", "#181c28", "#000000"];

const BASEMAPS = {
  "carto-dark": {
    tiles: ["a", "b", "c", "d"].map((s) => `https://${s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png`),
    attribution: "© OpenStreetMap contributors © CARTO",
  },
  "carto-light": {
    tiles: ["a", "b", "c", "d"].map((s) => `https://${s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png`),
    attribution: "© OpenStreetMap contributors © CARTO",
  },
  osm: {
    tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
    attribution: "© OpenStreetMap contributors",
  },
};

let cfg = null;
let map = null;
let ATTRS = null;

const state = {
  attr: "yr",
  ramp: "london",
  reverse: false,
  yrLo: 1650,
  yrHi: 2026,
  showUnk: true,
  cats: new Set([0, 1, 2, 3, 4, 5, 6, 7]),
  mainOnly: false,
  threeD: false,
  hMult: 5,
  opacity: 1,
  basemap: "none",
  bg: BG_COLORS[0],
};

/* ------------------------------------------------- boot */
fetch("data/config.json")
  .then((r) => {
    if (!r.ok) throw new Error("config.json not found — run the pipeline first");
    return r.json();
  })
  .then((c) => {
    cfg = c;
    buildAttrConfig();
    initUI();
    initMap();
  })
  .catch((e) => {
    $("loading").innerHTML = "⚠ " + e.message;
  });

function buildAttrConfig() {
  const yrLo = Math.max(1850, Math.floor((cfg.year.p2 - 10) / 10) * 10);
  ATTRS = {
    yr: { label: "Year built (age)", type: "cont", domain: [yrLo, cfg.year.max], scale: "linear",
          fmtV: (v) => String(Math.round(v)) },
    cat: { label: "Building type", type: "cat" },
    st: { label: "Stories (height)", type: "cont", domain: [1, Math.min(30, cfg.stories.max)], scale: "log",
          fmtV: (v) => (v >= 10 ? Math.round(v) : v.toFixed(1)) },
    sqft: { label: "Building size (assessor sq ft)", type: "cont", domain: [cfg.sqft.p5, cfg.sqft.p99], scale: "log",
            fmtV: (v) => fmt.format(Math.round(v)) },
    fpa: { label: "Footprint area (sq ft)", type: "cont", domain: [cfg.fpa.p5, cfg.fpa.p99], scale: "log",
           fmtV: (v) => fmt.format(Math.round(v)) },
    val: { label: "Improvement value ($)", type: "cont", domain: [Math.max(10000, cfg.val.p5), cfg.val.p99], scale: "log",
           fmtV: (v) => "$" + fmt.format(Math.round(v)) },
  };
  if (cfg.vpsf) {
    // computed live from val/sqft already present in tiles — surfaces
    // under-improved large buildings (low $/ft²) and premium small ones
    ATTRS.vpsf = {
      label: "Improvement $ per sq ft", type: "cont", scale: "log",
      domain: [Math.max(5, cfg.vpsf.p5), cfg.vpsf.p99],
      valueExpr: ["/", ["coalesce", ["get", "val"], 0],
                  ["max", ["coalesce", ["get", "sqft"], 1], 1]],
      unknownExpr: ["any", ["<=", ["coalesce", ["get", "val"], 0], 0],
                    ["<=", ["coalesce", ["get", "sqft"], 0], 0]],
      fmtV: (v) => "$" + (v < 10 ? v.toFixed(1) : fmt.format(Math.round(v))),
    };
  }
  if (cfg.nveh) {
    // p99 is skewed by apartment complexes and dealer lots (100s of vehicles);
    // clamp the color ramp to the residential 1-10 range, extremes saturate
    ATTRS.nveh = { label: "Vehicles at address", type: "cont",
                   domain: [1, 10], scale: "linear",
                   fmtV: (v) => (v >= 10 ? "10+" : String(Math.round(v))) };
    ATTRS.ppv = { label: "Personal property value ($)", type: "cont",
                  domain: [Math.max(500, cfg.ppv.p5), cfg.ppv.p99], scale: "log",
                  fmtV: (v) => "$" + fmt.format(Math.round(v)) };
  }
  state.yrLo = cfg.year.min;
  state.yrHi = cfg.year.max;
}

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
function initMap() {
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

  map.on("load", () => {
    map.addSource("bld", { type: "vector", url: "pmtiles://" + pmUrl, promoteId: undefined });

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

    if (!location.hash || location.hash.length < 4) {
      map.fitBounds([[cfg.bounds[0], cfg.bounds[1]], [cfg.bounds[2], cfg.bounds[3]]], { padding: 30, duration: 0 });
    }
    wireHover();
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

function heightExpr() {
  const perStory = 3.5 * (state.hMult / 5);
  return ["*", ["max", ["coalesce", ["get", "st"], 1], 1], perStory];
}

function refreshColors() {
  map.setPaintProperty("bld-fill", "fill-color", colorExpr());
  map.setPaintProperty("bld-3d", "fill-extrusion-color", colorExpr());
  renderLegend();
}

function refreshFilter() {
  const f = filterExpr();
  for (const l of ["bld-fill", "bld-line", "bld-3d"]) map.setFilter(l, f === true ? null : f);
}

/* ------------------------------------------------- legend */
function renderLegend() {
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

/* ------------------------------------------------- hover + click */
function featHTML(p, compactOnly) {
  const cat = CATS[p.cat] || CATS[0];
  const yr = p.yr > 0 ? p.yr : "unknown";
  let rows = "";
  const add = (k, v) => { rows += `<span class="k">${k}</span><span>${v}</span>`; };
  add("Year built", `<b>${yr}</b>`);
  add("Type", cat.label);
  if (p.st) add("Stories", p.st);
  if (p.sqft) add("Bldg area", fmt.format(p.sqft) + " ft²");
  if (p.fpa) add("Footprint", fmt.format(p.fpa) + " ft²");
  if (p.val) add("Impr. value", fmtUSD.format(p.val));
  if (p.val > 0 && p.sqft > 0) add("$ / sq ft", "$" + Math.round(p.val / p.sqft));
  if (p.nveh) add("Vehicles", p.nveh);
  if (p.ppv) add("Pers. property", fmtUSD.format(p.ppv));
  const veh = p.veh ? `<div class="tt-veh">${p.veh}</div>` : "";
  const addr = p.addr ? p.addr + (p.city ? ", " + p.city : "") : (compactOnly ? "" : "No address on parcel");
  return { addr, rows, veh };
}

function wireHover() {
  const tt = $("tooltip");
  let raf = null;
  let popup = null;
  // touch browsers synthesize mousemove from taps — the hover tooltip would
  // appear under the tap popup and never clear, so only wire it where a real
  // hover pointer exists
  const canHover = window.matchMedia("(hover: hover)").matches;
  if (canHover) {
    map.on("mousemove", (e) => {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = null;
        const fs = map.queryRenderedFeatures(e.point, { layers: ["bld-fill", "bld-3d"] });
        if (fs.length) {
          map.getCanvas().style.cursor = "pointer";
          const { addr, rows, veh } = featHTML(fs[0].properties, true);
          tt.innerHTML = (addr ? `<div class="tt-addr">${addr}</div>` : "") +
            `<div class="tt-line pp-grid">${rows}</div>` + veh;
          tt.hidden = false;
          const x = Math.min(e.point.x + 14, window.innerWidth - 260);
          const y = Math.min(e.point.y + 14, window.innerHeight - 140);
          tt.style.left = x + "px";
          tt.style.top = y + "px";
        } else {
          map.getCanvas().style.cursor = "";
          tt.hidden = true;
        }
      });
    });
    map.on("mouseout", () => { tt.hidden = true; });
  }
  map.on("click", (e) => {
    // dispatch features sit above buildings — they win the click
    const dspLayers = ["dsp-pts", "dsp-grid"].filter((l) => map.getLayer(l));
    if (dspLayers.length) {
      const df = map.queryRenderedFeatures(e.point, { layers: dspLayers });
      if (df.length) {
        if (popup) popup.remove();
        const p = df[0].properties;
        const html = df[0].layer.id === "dsp-pts"
          ? `<div class="pp-addr">${p.t}</div><div class="pp-grid">` +
            `<span class="k">Where</span><span>${p.loc}</span>` +
            `<span class="k">When</span><span>${new Date(p.ts).toLocaleString()}</span>` +
            `<span class="k">Category</span><span>${p.c}</span></div>` +
            `<div class="tt-veh">Call-for-service record, delayed 30 min–8 hr. Not a confirmed crime or report.</div>`
          : `<div class="pp-addr">${p.n} dispatches · last 30 days</div>` +
            `<div class="tt-veh">${p.top || ""}<br>≈500 ft grid cell</div>`;
        popup = new maplibregl.Popup({ closeButton: true, maxWidth: "300px" })
          .setLngLat(e.lngLat).setHTML(html).addTo(map);
        return;
      }
    }
    const fs = map.queryRenderedFeatures(e.point, { layers: ["bld-fill", "bld-3d"] });
    if (popup) {
      popup.remove();
      popup = null;
    }
    if (!fs.length) return;
    tt.hidden = true;
    const { addr, rows, veh } = featHTML(fs[0].properties, false);
    popup = new maplibregl.Popup({ closeButton: true, maxWidth: "290px" })
      .setLngLat(e.lngLat)
      .setHTML(`<div class="pp-addr">${addr}</div><div class="pp-grid">${rows}</div>` + veh)
      .addTo(map);
  });
}

/* ------------------------------------------------- UI wiring */
function initUI() {
  // stats
  const pct = ((cfg.year.known / cfg.count) * 100).toFixed(0);
  $("stats").textContent = `${fmt.format(cfg.count)} buildings · ${pct}% dated · assessor data ${cfg.cama_date}`;
  $("aboutMeta").textContent = `Buildings: ${fmt.format(cfg.count)} · generated ${cfg.generated} · CAMA export ${cfg.cama_date}`;

  // attribute select
  for (const [k, a] of Object.entries(ATTRS)) {
    const o = document.createElement("option");
    o.value = k;
    o.textContent = a.label;
    $("attr").appendChild(o);
  }
  $("attr").value = state.attr;
  $("attr").onchange = () => { state.attr = $("attr").value; refreshColors(); };

  // ramp select
  for (const [k, p] of Object.entries(PALETTES)) {
    const o = document.createElement("option");
    o.value = k;
    o.textContent = p.label;
    $("ramp").appendChild(o);
  }
  $("ramp").value = state.ramp;
  $("ramp").onchange = () => { state.ramp = $("ramp").value; refreshColors(); };
  $("reverse").onchange = () => { state.reverse = $("reverse").checked; refreshColors(); };

  // year range
  const lo = $("yrLo"), hi = $("yrHi");
  lo.min = hi.min = cfg.year.min;
  lo.max = hi.max = cfg.year.max;
  lo.value = state.yrLo;
  hi.value = state.yrHi;
  const showYr = () => { $("yrShow").textContent = `${state.yrLo} – ${state.yrHi}`; };
  showYr();
  const onRange = (ev) => {
    let a = Number(lo.value), b = Number(hi.value);
    if (a > b) { if (ev.target === lo) b = a; else a = b; lo.value = a; hi.value = b; }
    state.yrLo = a; state.yrHi = b;
    showYr();
    refreshFilter();
  };
  lo.oninput = onRange;
  hi.oninput = onRange;
  $("showUnk").onchange = () => { state.showUnk = $("showUnk").checked; refreshFilter(); };

  // category chips
  const chips = $("catChips");
  for (const k of [1, 5, 6, 3, 2, 4, 7, 0]) {
    const c = CATS[k];
    const el = document.createElement("div");
    el.className = "chip on";
    el.textContent = c.label;
    el.style.background = c.color;
    el.style.borderColor = c.color;
    el.onclick = () => {
      if (state.cats.has(k)) {
        state.cats.delete(k);
        el.classList.remove("on");
        el.style.background = "var(--ctl-bg)";
        el.style.borderColor = "var(--ctl-border)";
      } else {
        state.cats.add(k);
        el.classList.add("on");
        el.style.background = c.color;
        el.style.borderColor = c.color;
      }
      refreshFilter();
    };
    chips.appendChild(el);
  }

  // toggles
  $("mainOnly").onchange = () => { state.mainOnly = $("mainOnly").checked; refreshFilter(); };
  $("threeD").onchange = () => {
    state.threeD = $("threeD").checked;
    $("threeDOpts").hidden = !state.threeD;
    map.setLayoutProperty("bld-3d", "visibility", state.threeD ? "visible" : "none");
    map.setLayoutProperty("bld-fill", "visibility", state.threeD ? "none" : "visible");
    map.easeTo({ pitch: state.threeD ? 55 : 0, duration: 800 });
  };
  $("hMult").oninput = () => {
    state.hMult = Number($("hMult").value);
    $("hMultV").textContent = state.hMult;
    map.setPaintProperty("bld-3d", "fill-extrusion-height", heightExpr());
  };
  $("opacity").oninput = () => {
    state.opacity = Number($("opacity").value) / 100;
    $("opV").textContent = $("opacity").value + "%";
    map.setPaintProperty("bld-fill", "fill-opacity", state.opacity);
    map.setPaintProperty("bld-3d", "fill-extrusion-opacity", Math.min(0.98, state.opacity));
  };

  // basemap
  $("basemap").onchange = () => {
    state.basemap = $("basemap").value;
    if (map.getLayer("basemap")) map.removeLayer("basemap");
    if (map.getSource("basemap")) map.removeSource("basemap");
    if (state.basemap !== "none") {
      const bm = BASEMAPS[state.basemap];
      map.addSource("basemap", { type: "raster", tiles: bm.tiles, tileSize: 256, attribution: bm.attribution });
      map.addLayer({ id: "basemap", type: "raster", source: "basemap", paint: { "raster-opacity": 0.85 } }, "bld-fill");
    }
  };

  // background swatches
  const sw = $("bgSwatches");
  BG_COLORS.forEach((c, i) => {
    const el = document.createElement("div");
    el.className = "bgsw" + (i === 0 ? " on" : "");
    el.style.background = c;
    el.onclick = () => {
      state.bg = c;
      map.setPaintProperty("bg", "background-color", c);
      document.querySelectorAll(".bgsw").forEach((x) => x.classList.remove("on"));
      el.classList.add("on");
    };
    sw.appendChild(el);
  });

  // panel toggle (slide-away drawer)
  const panel = $("panel"), pt = $("panelToggle");
  const setPanel = (hidden) => {
    panel.classList.toggle("hidden", hidden);
    pt.classList.toggle("show", hidden);
  };
  pt.onclick = () => setPanel(false);
  $("panelClose").onclick = () => setPanel(true);
  // phones: start with the map full-screen, panel tucked away
  if (window.matchMedia("(max-width: 640px)").matches) setPanel(true);
  document.addEventListener("keydown", (e) => {
    if (e.key === "h" || e.key === "H") {
      if (/input|select|textarea/i.test(document.activeElement.tagName)) return;
      setPanel(!panel.classList.contains("hidden"));
    }
  });

  // about
  $("aboutLink").onclick = (e) => { e.preventDefault(); $("about").hidden = false; };
  $("aboutClose").onclick = () => { $("about").hidden = true; };
  $("about").onclick = (e) => { if (e.target === $("about")) $("about").hidden = true; };

  initDispatch();
  renderLegend();
}

/* ------------------------------------------------- public dispatch overlay */
const DSP_BASE = "https://raw.githubusercontent.com/brandongrant/pulaski_building_map/data/dispatch/out";
const DSP_CATS = {
  "Alarm": { k: "al", color: "#e8c15a" },
  "Traffic": { k: "tr", color: "#6fa8dc" },
  "Property": { k: "pr", color: "#e07a5f" },
  "Disturbance": { k: "di", color: "#f28cb1" },
  "Person/Welfare": { k: "pw", color: "#8bd3c7" },
  "Suspicious": { k: "su", color: "#b39ddb" },
  "Animal": { k: "an", color: "#90be6d" },
  "Administrative": { k: "ad", color: "#9aa3b5" },
  "Other": { k: "ot", color: "#cfcfcf" },
};
const dsp = { on: false, mode: "24h", cats: new Set(Object.keys(DSP_CATS)), loaded: false };

function dspCircleColor() {
  const m = ["match", ["get", "c"]];
  for (const [name, d] of Object.entries(DSP_CATS)) m.push(name, d.color);
  m.push("#cfcfcf");
  return m;
}

function dspFilter() {
  return dsp.cats.size === Object.keys(DSP_CATS).length
    ? null : ["in", ["get", "c"], ["literal", [...dsp.cats]]];
}

function dspGridColor() {
  const sum = ["+"];
  for (const name of dsp.cats) sum.push(["coalesce", ["get", DSP_CATS[name].k], 0]);
  const v = sum.length > 1 ? sum : 0;
  return ["interpolate", ["linear"], v,
          0, "rgba(70,80,100,0.08)", 1, "#27476b", 5, "#3d7fb0",
          15, "#e8c15a", 40, "#e07a5f", 100, "#ff4d4d"];
}

function dspStats() {
  return fetch(DSP_BASE + "/stats.json", { cache: "no-store" })
    .then((r) => (r.ok ? r.json() : null)).catch(() => null);
}

async function dspLoad() {
  if (dsp.loaded) return true;
  const s = await dspStats();
  if (!s) {
    $("dspSince").textContent = "· no data collected yet";
    return false;
  }
  map.addSource("dsp30", { type: "geojson", data: DSP_BASE + "/grid_30d.geojson" });
  map.addSource("dsp7", { type: "geojson", data: DSP_BASE + "/recent_7d.geojson" });
  map.addSource("dsp24", { type: "geojson", data: DSP_BASE + "/recent_24h.geojson" });
  map.addLayer({
    id: "dsp-grid", type: "fill", source: "dsp30", layout: { visibility: "none" },
    paint: { "fill-color": dspGridColor(), "fill-opacity": 0.55,
             "fill-outline-color": "rgba(0,0,0,0.35)" },
  });
  map.addLayer({
    id: "dsp-heat", type: "heatmap", source: "dsp7", layout: { visibility: "none" },
    paint: {
      "heatmap-radius": ["interpolate", ["linear"], ["zoom"], 9, 6, 13, 22, 16, 40],
      "heatmap-intensity": ["interpolate", ["linear"], ["zoom"], 9, 0.6, 15, 1.6],
      "heatmap-opacity": 0.75,
    },
  });
  map.addLayer({
    id: "dsp-pts", type: "circle", source: "dsp24", layout: { visibility: "none" },
    paint: {
      "circle-color": dspCircleColor(),
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 2.5, 13, 5.5, 16, 8],
      "circle-stroke-color": "#000000", "circle-stroke-width": 0.8,
      "circle-opacity": 0.92,
    },
  });
  dsp.loaded = true;
  return true;
}

function dspRefresh() {
  if (!dsp.loaded) return;
  const vis = {
    "dsp-pts": dsp.on && dsp.mode === "24h",
    "dsp-heat": dsp.on && dsp.mode === "7d",
    "dsp-grid": dsp.on && dsp.mode === "30d",
  };
  for (const [l, v] of Object.entries(vis)) {
    map.setLayoutProperty(l, "visibility", v ? "visible" : "none");
  }
  const f = dspFilter();
  map.setFilter("dsp-pts", f);
  map.setFilter("dsp-heat", f);
  map.setPaintProperty("dsp-grid", "fill-color", dspGridColor());
}

function initDispatch() {
  const chips = $("dspCats");
  for (const [name, d] of Object.entries(DSP_CATS)) {
    const el = document.createElement("div");
    el.className = "chip on";
    el.textContent = name;
    el.style.background = d.color;
    el.style.borderColor = d.color;
    el.onclick = () => {
      if (dsp.cats.has(name)) {
        dsp.cats.delete(name);
        el.classList.remove("on");
        el.style.background = "var(--ctl-bg)";
        el.style.borderColor = "var(--ctl-border)";
      } else {
        dsp.cats.add(name);
        el.classList.add("on");
        el.style.background = d.color;
        el.style.borderColor = d.color;
      }
      dspRefresh();
    };
    chips.appendChild(el);
  }
  $("dspOn").onchange = async () => {
    dsp.on = $("dspOn").checked;
    $("dspControls").hidden = !dsp.on;
    if (dsp.on) await dspLoad();
    dspRefresh();
  };
  document.querySelectorAll('input[name="dspMode"]').forEach((r) => {
    r.onchange = () => { dsp.mode = r.value; dspRefresh(); };
  });
  dspStats().then((s) => {
    if (s && s.collecting_since) {
      $("dspSince").textContent =
        `· ${fmt.format(s.total_collected)} calls since ${s.collecting_since.slice(0, 10)}`;
    }
  });
}

/* re-render legend once map ready (colors already set at layer creation) */
