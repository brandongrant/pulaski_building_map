/* Pulaski County Building Map — static config + attribute definitions */
import { fmt } from "./util.js";

export const UNKNOWN_COLOR = "#39404f";

export const CATS = {
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
export const PALETTES = {
  london: { label: "Colouring London", colors: ["#4daf9c", "#53a7c9", "#3d7fb0", "#3b5ba8", "#5d3b8c", "#8e1e5f", "#b31c1c", "#d92120", "#e75323", "#ef7b28", "#f6a33b", "#f3d54c", "#f7f1a1"] },
  amsterdam: { label: "Amsterdam fire", colors: ["#70040b", "#a81605", "#d24e0f", "#ee8f1e", "#f8c53c", "#f2e29b", "#bfe0e8", "#7fc0e0", "#4292c6", "#2166ac"] },
  viridis: { label: "Viridis", colors: ["#440154", "#46327e", "#365c8d", "#277f8e", "#1fa187", "#4ac16d", "#a0da39", "#fde725"] },
  magma: { label: "Magma", colors: ["#0a0722", "#1d1147", "#51127c", "#822681", "#b63679", "#e65164", "#fb8861", "#fec287", "#fcfdbf"] },
  turbo: { label: "Turbo", colors: ["#30123b", "#4145ab", "#4675ed", "#39a2fc", "#1bcfd4", "#24eca6", "#61fc6c", "#a4fc3b", "#d1e834", "#f3c63a", "#fe9b2d", "#f36315", "#d93806", "#b11901", "#7a0402"] },
  cividis: { label: "Cividis", colors: ["#00204d", "#00336f", "#39486b", "#575d6d", "#707173", "#8a8779", "#a69d75", "#c4b56c", "#e4cf5b", "#ffea46"] },
  coolwarm: { label: "Cool–Warm", colors: ["#3b4cc0", "#6688ee", "#88abfd", "#b8d0f9", "#dddddd", "#f5c4ac", "#f39475", "#dd604d", "#b40426"] },
};

export const BG_COLORS = ["#05060a", "#0b0e17", "#10131f", "#181c28", "#000000"];

export const BASEMAPS = {
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

// config.json payload (counts, domains, histograms from the pipeline); set at boot
export let cfg = null;
// attribute definitions derived from cfg; built once at boot
export let ATTRS = null;

export function buildAttrConfig(c) {
  cfg = c;
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
  // 311 requests join buildings by address at render time via feature-state
  // (overlays/requests311.js sets one state per address once the collected
  // data loads). Addresses appear in tiles from z13 up, so below that — and
  // anywhere without a collected request — buildings render as unknown.
  ATTRS.sr311 = {
    label: "311 requests at address (collected)", type: "cont",
    domain: [1, 10], scale: "linear",
    valueExpr: ["coalesce", ["feature-state", "sr"], 0],
    unknownExpr: ["<=", ["coalesce", ["feature-state", "sr"], 0], 0],
    fmtV: (v) => (v >= 10 ? "10+" : String(Math.round(v))),
  };
}
