/* Pulaski County Building Map — left control-panel wiring */
import { $, fmt } from "./util.js";
import { cfg, ATTRS, CATS, PALETTES, BASEMAPS, BG_COLORS } from "./config.js";
import { state, ui } from "./state.js";
import { map, refreshColors, refreshFilter, renderLegend, heightExpr } from "./map.js";
import { initSearch } from "./search.js";
import { initDispatch } from "./overlays/dispatch.js";
import { initPermits } from "./overlays/permits.js";
import { initDeeds } from "./overlays/deeds.js";
import { initVehicleSearch } from "./vehicle-search.js";

export function initUI() {
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
  ui.setPanel = setPanel;
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

  initSearch();
  initDispatch();
  initPermits();
  initVehicleSearch();
  initDeeds();
  renderLegend();
}
