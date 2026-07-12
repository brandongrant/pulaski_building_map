/* Pulaski County Building Map — entry point */
import { $ } from "./util.js";
import { buildAttrConfig, cfg } from "./config.js";
import { state } from "./state.js";
import { map, initMap } from "./map.js";
import { initUI } from "./controls.js";
import { wireHover, featHTML } from "./property-panel.js";
import { initProfile, openProfile } from "./profile.js";
import { loadServices } from "./api.js";
import { ownersLoad, searchRun, selectResult, parcelResolveStats } from "./search.js";
import { vehRun } from "./vehicle-search.js";

/* ------------------------------------------------- boot */
loadServices();

fetch("data/config.json")
  .then((r) => {
    if (!r.ok) throw new Error("config.json not found — run the pipeline first");
    return r.json();
  })
  .then((c) => {
    buildAttrConfig(c);
    // year-filter bounds come from the data
    state.yrLo = cfg.year.min;
    state.yrHi = cfg.year.max;
    initUI();
    initMap(wireHover);
    initProfile();
  })
  .catch((e) => {
    $("loading").innerHTML = "⚠ " + e.message;
  });

// Debug/verification handle. Module scope hides the app's internals, so the
// headless verification recipes (browser console / preview_eval) reach the
// same functions here instead of as bare globals.
window.__app = {
  get map() { return map; },
  get cfg() { return cfg; },
  state,
  parcelResolveStats,
  ownersLoad,
  searchRun,
  selectResult,
  featHTML,
  vehRun,
  openProfile,
};
