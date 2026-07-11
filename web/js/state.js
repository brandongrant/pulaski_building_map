/* Pulaski County Building Map — shared mutable view state */
import { BG_COLORS } from "./config.js";

export const state = {
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

// cross-module UI hooks, assigned during initUI (slide-away drawer control)
export const ui = { setPanel: null };
