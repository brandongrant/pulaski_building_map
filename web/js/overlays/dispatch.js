/* Pulaski County Building Map — public dispatch overlay */
import { $, fmt } from "../util.js";
import { map } from "../map.js";

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
  if (map.getLayer("hit-ring")) map.moveLayer("hit-ring"); // keep search hits on top
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

export function initDispatch() {
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
