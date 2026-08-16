/* ===========================================================================
   Pulse — the second view of the site.

   The map answers "what is here". This answers "what is happening, and when".
   It reads one small pre-rolled summary (pipeline/build_pulse.py) and draws it
   as hand-built SVG: a 24-hour dial, a 168-square week, a category
   leaderboard, an abstract mosaic of the city, and the case files parsed out
   of LRPD's daily report PDFs.

   The organising idea is that time, not place, is the useful axis here. The
   city is busiest in the afternoon and most violent near midnight, and no
   amount of scrolling through PDFs makes that visible.
   =========================================================================== */
(function () {
  "use strict";

  const PULSE_URLS = [
    "https://raw.githubusercontent.com/brandongrant/pulaski_building_map/data/pulse/out/pulse.json",
    "data/pulse/pulse.json",   // seed copy, and what `--out` writes for local dev
  ];
  const SVGNS = "http://www.w3.org/2000/svg";
  const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  // the "is this violence" set, used for the clock's inner ring
  const VIOLENT = ["shots", "assault", "robbery"];
  // week-on-week percentages off a handful of calls are noise, not news
  const MIN_BASE = 12;
  const STEEL = [63, 90, 134];
  const R = 6378137.0;

  const $ = (id) => document.getElementById(id);
  let D = null;             // the payload
  let sel = null;           // selected category key, or null for everything
  let hoverHour = null;
  let drawn = false;
  let fromSeed = false;     // true when the live summary could not be reached

  /* ------------------------------------------------------------- utilities */
  const fmt = (n) => (n == null ? "—" : n.toLocaleString());
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

  function catMeta(key) {
    // DSP_CATS lives in app.js and is the one place category colours are set.
    const t = (typeof DSP_CATS === "object" && DSP_CATS && DSP_CATS[key]) || null;
    return t || { label: key.charAt(0).toUpperCase() + key.slice(1), color: "#8b93a5" };
  }
  const catColor = (k) => catMeta(k).color;
  const catLabel = (k) => catMeta(k).label;

  function mix(a, b, t) {
    t = clamp(t, 0, 1);
    return `rgb(${a.map((v, i) => Math.round(v + (b[i] - v) * t)).join(",")})`;
  }
  function hexToRgb(h) {
    const m = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(h);
    return m ? [1, 2, 3].map((i) => parseInt(m[i], 16)) : [140, 148, 165];
  }
  function hourLabel(h) {
    const ap = h < 12 ? "AM" : "PM";
    return `${((h + 11) % 12) + 1} ${ap}`;
  }
  function el(tag, attrs, text) {
    const n = document.createElementNS(SVGNS, tag);
    for (const k in attrs) if (attrs[k] != null) n.setAttribute(k, attrs[k]);
    if (text != null) n.textContent = text;
    return n;
  }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

  /* --------------------------------------------------------------- tooltip */
  const tip = () => $("pTip");
  function showTip(evt, html) {
    const t = tip();
    t.innerHTML = html;
    t.hidden = false;
    const pad = 14;
    const w = t.offsetWidth, h = t.offsetHeight;
    t.style.left = clamp(evt.clientX + pad, 6, innerWidth - w - 6) + "px";
    t.style.top = clamp(evt.clientY - h - pad, 6, innerHeight - h - 6) + "px";
  }
  function hideTip() { tip().hidden = true; }

  /* ------------------------------------------------------------ view swap */
  // Overlay views that sit on top of the live map. The map div itself is never
  // hidden - it keeps its WebGL context - so only these ids get toggled.
  const OVERLAY_VIEWS = ["pulse", "watch"];

  function showView(v) {
    for (const name of OVERLAY_VIEWS) {
      const panel = $(name);
      if (panel) panel.hidden = name !== v;
      document.body.classList.toggle("view-" + name, name === v);
    }
    for (const b of document.querySelectorAll(".tabBtn")) {
      const on = b.dataset.view === v;
      b.classList.toggle("on", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
    }
    // Views other than Pulse own their own setup; tell them the view changed.
    document.dispatchEvent(new CustomEvent("viewchange", { detail: { view: v } }));
    if (v !== "map" && v !== "pulse") {
      try { history.replaceState(null, "", "#" + v); } catch (e) { /* file:// */ }
      return;
    }
    if (v === "pulse") {
      if (!drawn) load();
      try { history.replaceState(null, "", "#pulse"); } catch (e) { /* file:// */ }
    } else {
      try { history.replaceState(null, "", location.pathname + location.search); }
      catch (e) { /* ignore */ }
      // the map was sized while it was hidden behind the Pulse view
      if (typeof map !== "undefined" && map && map.resize) {
        requestAnimationFrame(() => map.resize());
      }
    }
  }

  function goToMap(lon, lat, zoom) {
    showView("map");
    if (typeof map === "undefined" || !map || !map.flyTo) return;
    const fly = () => {
      try { map.flyTo({ center: [lon, lat], zoom: zoom || 15.5, speed: 1.4 }); }
      catch (e) { map.jumpTo({ center: [lon, lat], zoom: zoom || 15.5 }); }
    };
    // isStyleLoaded, not loaded(): the latter also reads false while the map is
    // merely busy, and "load" has already fired by then, so a deferred fly-to
    // would never run.
    if (map.isStyleLoaded && map.isStyleLoaded()) fly();
    else map.once("load", fly);
  }

  /* ------------------------------------------------------------------ load */
  async function load() {
    // ?pulse=local skips the published summary and reads the checkout's own
    // copy — how you iterate on the pipeline without waiting for a collector run
    const urls = /[?&]pulse=local\b/.test(location.search)
      ? PULSE_URLS.slice(1) : PULSE_URLS;
    for (let i = 0; i < urls.length; i++) {
      try {
        const r = await fetch(urls[i], { cache: "no-store" });
        if (!r.ok) continue;
        D = await r.json();
        fromSeed = urls[i] !== PULSE_URLS[0];
        break;
      } catch (e) { /* try the next source */ }
    }
    if (!D) {
      $("pMeta").innerHTML =
        "Nothing collected yet — the summary is published by the scheduled " +
        "collector. Check back after its next run.";
      return;
    }
    drawn = true;
    window.__pulse = { data: () => D, select, showView };
    renderAll();
  }

  function select(cat) {
    sel = sel === cat ? null : cat;
    const bar = $("pFilterBar");
    if (sel) {
      const m = catMeta(sel);
      bar.hidden = false;
      const chip = $("pFilterChip");
      chip.textContent = m.label.toLowerCase();
      chip.style.color = m.color;
    } else {
      bar.hidden = true;
    }
    renderAll();
  }

  /* ------------------------------------------------------------- rendering */
  function renderAll() {
    renderMeta();
    renderNow();
    renderClock();
    renderWeek();
    renderCats();
    renderHex();
    renderStreets();
    renderRisk();
    renderCases();
    renderHistory();
  }

  function renderMeta() {
    const c = D.calls, r = D.reports || {};
    const since = c.since ? new Date(c.since) : null;
    const upd = D.updated ? new Date(D.updated) : null;
    $("pMeta").innerHTML =
      `<span class="pDot"${fromSeed ? ' style="background:#e8c15a;box-shadow:none"' : ""}></span>` +
      `<b>${fmt(c.total)}</b> calls for service` +
      (since ? ` since ${since.toLocaleDateString(undefined, { month: "long", day: "numeric" })}` : "") +
      ` · <b>${fmt(r.incidents)}</b> incident reports from <b>${fmt(r.collected)}</b> daily PDFs` +
      (upd ? ` · ${fromSeed ? "snapshot from" : "updated"} ` +
             upd.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })
           : "") +
      (fromSeed ? ` <span style="color:#e8c15a">(couldn't reach the live summary — showing the bundled snapshot)</span>` : "");
  }

  /* --- now: headline figures + a 60-day daily bar sparkline --------------- */
  function renderNow() {
    const c = D.calls;
    const d7 = sel ? (c.by_cat_7d || {})[sel] || 0 : c.d7;
    const p7 = sel ? (c.by_cat_prev7 || {})[sel] || 0 : c.prev7;
    const delta = p7 >= MIN_BASE ? Math.round(((d7 - p7) / p7) * 100) : null;
    const dcls = delta == null ? "flat" : delta > 4 ? "up" : delta < -4 ? "down" : "flat";
    const arrow = delta == null ? "" : delta > 0 ? "▲" : delta < 0 ? "▼" : "▬";
    const noun = sel ? catLabel(sel).toLowerCase() : "calls";

    const peak = peakHour();
    const vio = VIOLENT.reduce((s, k) => s + sum(D.clock[k] || []), 0);
    const vpeak = peakHour(VIOLENT);
    const perDay = d7 / 7;

    const figs = [
      { v: perDay >= 10 ? fmt(Math.round(perDay)) : perDay.toFixed(1),
        k: `${noun} a day`, d: "" },
      { v: fmt(d7 || 0), k: "in the last 7 days",
        d: delta == null ? "" : `<span class="${dcls}">${arrow} ${Math.abs(delta)}% vs the week before</span>` },
      { v: hourLabel(peak.h), k: sel ? "busiest hour for this" : "busiest hour",
        d: `<span class="flat">${fmt(peak.v)} in that hour, last ${windowDays()} days</span>` },
    ];
    if (!sel && vio) {
      figs.push({ v: hourLabel(vpeak.h), k: "peak for violence",
                  d: `<span class="flat">shots · assault · robbery</span>` });
    }
    $("pNowFigs").innerHTML = figs.map((f) =>
      `<div class="pFig"><div class="v">${f.v}</div><div class="k">${f.k}</div>` +
      (f.d ? `<div class="d">${f.d}</div>` : "") + `</div>`).join("");
    const mv = movers();
    $("pMovers").innerHTML = mv;
    $("pMovers").hidden = !mv;

    // daily bars
    const svg = $("pDaySpark");
    clear(svg);
    const days = D.days || { labels: [], total: [], by_cat: {} };
    const vals = sel ? ((days.by_cat || {})[sel] || []) : (days.total || []);
    const n = vals.length;
    if (!n) return;
    const max = Math.max(1, ...vals);
    const w = 720 / n, col = sel ? catColor(sel) : "#4d7fb8";
    vals.forEach((v, i) => {
      const h = Math.max(1, (v / max) * 78);
      const rect = el("rect", {
        x: (i * w + 0.6).toFixed(2), y: (84 - h).toFixed(2),
        width: Math.max(1, w - 1.2).toFixed(2), height: h.toFixed(2),
        fill: col, opacity: 0.55 + 0.45 * (v / max), rx: 1,
      });
      rect.addEventListener("pointerenter", (e) => showTip(e,
        `<b>${dayLabel(days.labels[i])}</b><span class="l">${fmt(v)} ${noun}</span>`));
      rect.addEventListener("pointerleave", hideTip);
      svg.appendChild(rect);
    });
    $("pSparkA").textContent = dayLabel(days.labels[0]);
    $("pSparkB").textContent = dayLabel(days.labels[n - 1]);
  }

  /* What actually changed this week. Categories with a thin base swing wildly
     on noise, so a small prior week is left out entirely. */
  function movers() {
    const a = D.calls.by_cat_7d || {}, b = D.calls.by_cat_prev7 || {};
    const rows = D.cat_order
      .filter((k) => (b[k] || 0) >= MIN_BASE)
      .map((k) => [k, Math.round((((a[k] || 0) - b[k]) / b[k]) * 100)])
      .sort((x, y) => y[1] - x[1]);
    if (rows.length < 2) return "";
    const [uk, uv] = rows[0], [dk, dv] = rows[rows.length - 1];
    const say = (k, v) =>
      `<b style="color:${catColor(k)}">${catLabel(k).toLowerCase()}</b> ` +
      `<span class="${v > 0 ? "up" : "down"}">${v > 0 ? "+" : ""}${v}%</span>`;
    if (uv <= 0 && dv >= 0) return "";
    return `Against the week before: ${uv > 0 ? "up most " + say(uk, uv) : ""}` +
      `${uv > 0 && dv < 0 ? ", " : ""}${dv < 0 ? "down most " + say(dk, dv) : ""}.`;
  }

  const sum = (a) => a.reduce((s, v) => s + v, 0);
  /* The clock and the week grid cover a rolling window; early on, collection is
     shorter than the window, so per-day figures must divide by whichever is
     smaller. */
  function windowDays() {
    return Math.max(1, Math.min(D.calls.clock_days, D.calls.days_collected || 1));
  }
  function dayLabel(iso) {
    if (!iso) return "";
    const [y, m, d] = iso.split("-").map(Number);
    return new Date(y, m - 1, d).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }
  function hourTotals(cats) {
    const out = new Array(24).fill(0);
    const keys = cats || Object.keys(D.clock);
    for (const k of keys) {
      const a = D.clock[k];
      if (a) for (let h = 0; h < 24; h++) out[h] += a[h];
    }
    return out;
  }
  function peakHour(cats) {
    const t = hourTotals(sel ? [sel] : cats);
    let h = 0;
    for (let i = 1; i < 24; i++) if (t[i] > t[h]) h = i;
    return { h, v: t[h], series: t };
  }

  /* --- the clock ---------------------------------------------------------- */
  function renderClock() {
    const svg = $("pClock");
    clear(svg);
    const cx = 210, cy = 210;
    const all = hourTotals();
    const inner = sel ? (D.clock[sel] || new Array(24).fill(0)) : hourTotals(VIOLENT);
    const allMax = Math.max(1, ...all), innMax = Math.max(1, ...inner);
    const nowH = new Date().getHours();

    for (const r of [58, 92, 126]) {
      svg.appendChild(el("circle", { cx, cy, r, fill: "none",
        stroke: "rgba(255,255,255,0.05)", "stroke-width": 1 }));
    }

    const arc = (h, r0, r1) => {
      const a0 = ((h * 15 - 90 - 7.5 + 0.9) * Math.PI) / 180;
      const a1 = ((h * 15 - 90 + 7.5 - 0.9) * Math.PI) / 180;
      const p = (a, r) => `${(cx + r * Math.cos(a)).toFixed(2)} ${(cy + r * Math.sin(a)).toFixed(2)}`;
      return `M ${p(a0, r0)} L ${p(a0, r1)} A ${r1} ${r1} 0 0 1 ${p(a1, r1)} ` +
             `L ${p(a1, r0)} A ${r0} ${r0} 0 0 0 ${p(a0, r0)} Z`;
    };

    const innColor = hexToRgb(sel ? catColor(sel) : "#ff4d4d");
    for (let h = 0; h < 24; h++) {
      // outer band: every call, radius by volume
      const rOut = 130 + (all[h] / allMax) * 66;
      const wedge = el("path", {
        d: arc(h, 130, rOut), class: "wedge",
        fill: mix(STEEL, [150, 190, 240], all[h] / allMax),
        opacity: 0.5 + 0.45 * (all[h] / allMax),
        stroke: h === nowH ? "rgba(243,213,76,0.9)" : "none",
        "stroke-width": h === nowH ? 1.2 : 0,
      });
      // inner band: the selected slice, on its own scale so the shape is legible
      const rIn = 56 + (inner[h] / innMax) * 66;
      const wedge2 = el("path", {
        d: arc(h, 56, rIn), class: "wedge",
        fill: mix(innColor.map((v) => v * 0.35), innColor, inner[h] / innMax),
        opacity: 0.55 + 0.4 * (inner[h] / innMax),
      });
      for (const w of [wedge, wedge2]) {
        w.addEventListener("pointerenter", () => setHour(h));
        w.addEventListener("pointerleave", () => setHour(null));
        svg.appendChild(w);
      }
    }

    for (let h = 0; h < 24; h += 3) {
      const a = ((h * 15 - 90) * Math.PI) / 180;
      svg.appendChild(el("text", {
        x: cx + 208 * Math.cos(a), y: cy + 208 * Math.sin(a) + 4,
        "text-anchor": "middle", class: h === nowH ? "hnow" : null,
      }, h === 0 ? "12a" : h === 12 ? "12p" : h < 12 ? h + "a" : (h - 12) + "p"));
    }

    $("pClockKey").innerHTML =
      `<span><i class="pKeySw" style="background:${mix(STEEL, [150, 190, 240], 0.8)}"></i>every call</span>` +
      `<span><i class="pKeySw" style="background:${sel ? catColor(sel) : "#ff4d4d"}"></i>` +
      `${sel ? catLabel(sel).toLowerCase() : "shots · assault · robbery"}</span>` +
      `<span style="opacity:.75">each ring is scaled to its own busiest hour</span>`;
    setHour(null);
  }

  function setHour(h) {
    hoverHour = h;
    const read = $("pClockRead");
    // the readout follows the selection, so the average and the peak always
    // describe the same series
    const series = sel ? (D.clock[sel] || new Array(24).fill(0)) : hourTotals();
    const all = hourTotals();
    const days = windowDays();
    if (h == null) {
      let peak = 0, quiet = 0;
      for (let i = 1; i < 24; i++) {
        if (series[i] > series[peak]) peak = i;
        if (series[i] < series[quiet]) quiet = i;
      }
      const per = sum(series) / days;
      read.innerHTML =
        `<div class="h">${per >= 10 ? fmt(Math.round(per)) : per.toFixed(1)}</div>` +
        `<div class="n">${sel ? catLabel(sel).toLowerCase() : "calls"} a day</div>` +
        `<div class="t">Peaks at <b>${hourLabel(peak)}</b>,<br>quietest at <b>${hourLabel(quiet)}</b></div>`;
    } else {
      const tops = Object.keys(D.clock)
        .map((k) => [k, D.clock[k][h]])
        .filter((x) => x[1] > 0)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 3);
      read.innerHTML =
        `<div class="h">${hourLabel(h)}</div>` +
        `<div class="n">${fmt(all[h])} calls · ${(all[h] / days).toFixed(1)}/day</div>` +
        `<div class="t">` + tops.map(([k, v]) =>
          `<span style="color:${catColor(k)}">■</span> ${catLabel(k).toLowerCase()} ${fmt(v)}`)
          .join("<br>") + `</div>`;
    }
    highlightWeekColumn(h);
  }

  /* --- the week ----------------------------------------------------------- */
  function renderWeek() {
    const svg = $("pWeek");
    clear(svg);
    const grid = sel ? (D.dow_hour_cat || {})[sel] : D.dow_hour;
    const pad = { l: 30, t: 16, r: 4, b: 20 };
    const cw = (460 - pad.l - pad.r) / 24, ch = (210 - pad.t - pad.b) / 7;
    if (!grid || !grid.some((row) => row.some(Boolean))) {
      svg.appendChild(el("text", { x: 230, y: 105, "text-anchor": "middle" },
        "nothing recorded in this category yet"));
      $("pWeekNote").textContent = "";
      return;
    }
    let max = 1, peak = { v: -1 };
    grid.forEach((row, d) => row.forEach((v, h) => {
      if (v > max) max = v;
      if (v > peak.v) peak = { v, d, h };
    }));
    const base = hexToRgb(sel ? catColor(sel) : "#f3d54c");
    const now = new Date(), nd = (now.getDay() + 6) % 7, nh = now.getHours();

    for (let d = 0; d < 7; d++) {
      svg.appendChild(el("text", { x: 4, y: pad.t + d * ch + ch / 2 + 3.4 }, DOW[d]));
      for (let h = 0; h < 24; h++) {
        const v = grid[d][h];
        const t = Math.pow(v / max, 0.62);
        const cell = el("rect", {
          class: "cell", x: (pad.l + h * cw + 0.6).toFixed(2),
          y: (pad.t + d * ch + 0.6).toFixed(2),
          width: (cw - 1.2).toFixed(2), height: (ch - 1.2).toFixed(2), rx: 2,
          fill: v ? mix([22, 27, 41], base, t) : "rgba(255,255,255,0.028)",
          stroke: d === nd && h === nh ? "rgba(255,255,255,0.75)" : null,
          "stroke-width": d === nd && h === nh ? 1.2 : null,
        });
        cell.addEventListener("pointerenter", (e) => showTip(e,
          `<b>${DOW[d]} ${hourLabel(h)}</b><span class="l">${fmt(v)} ` +
          `${sel ? catLabel(sel).toLowerCase() : "calls"} over ${windowDays()} days</span>`));
        cell.addEventListener("pointerleave", hideTip);
        svg.appendChild(cell);
      }
    }
    for (const h of [0, 6, 12, 18]) {
      svg.appendChild(el("text", {
        x: pad.l + h * cw + cw / 2, y: 206, "text-anchor": "middle",
      }, h === 0 ? "12a" : h === 12 ? "12p" : h < 12 ? h + "a" : (h - 12) + "p"));
    }
    svg.appendChild(el("rect", {
      id: "pWeekCol", x: -99, y: pad.t - 3, width: cw, height: 7 * ch + 6,
      fill: "none", stroke: "rgba(255,255,255,0.5)", "stroke-width": 1, rx: 3,
      "pointer-events": "none",
    }));
    svg.dataset.padL = pad.l;
    svg.dataset.cw = cw;
    $("pWeekNote").innerHTML = peak.v > 0
      ? `Worst square on the board: <b>${DOW[peak.d]} at ${hourLabel(peak.h)}</b> — ` +
        `${fmt(peak.v)} ${sel ? catLabel(sel).toLowerCase() : "calls"} across the ` +
        `last ${windowDays()} days.`
      : "";
  }

  function highlightWeekColumn(h) {
    const svg = $("pWeek"), col = $("pWeekCol");
    if (!col || !svg) return;
    if (h == null) { col.setAttribute("x", -99); return; }
    col.setAttribute("x", (+svg.dataset.padL + h * +svg.dataset.cw).toFixed(2));
  }

  /* --- category leaderboard ----------------------------------------------- */
  function renderCats() {
    const box = $("pCats");
    box.innerHTML = "";
    const totals = D.cat_order.map((k) => [k, sum(D.clock[k] || [])]);
    const max = Math.max(1, ...totals.map((t) => t[1]));
    const d7 = D.calls.by_cat_7d || {}, p7 = D.calls.by_cat_prev7 || {};
    for (const [k, n] of totals) {
      const m = catMeta(k);
      const a = d7[k] || 0, b = p7[k] || 0;
      const delta = b >= MIN_BASE ? Math.round(((a - b) / b) * 100) : null;
      const dcls = delta == null ? "flat" : delta > 9 ? "up" : delta < -9 ? "down" : "flat";
      const row = document.createElement("button");
      row.className = "pCatRow" + (sel === k ? " sel" : "");
      row.innerHTML =
        `<i class="pCatSw" style="background:${m.color}"></i>` +
        `<span class="pCatName">${m.label}</span>` +
        `<span class="pCatBar"><i style="width:${(n / max) * 100}%;background:${m.color}"></i></span>` +
        `<span class="pCatFig"><b>${fmt(a)}</b>` +
        (delta == null ? "" : `<span class="d ${dcls}">${delta > 0 ? "+" : ""}${delta}%</span>`) +
        `</span>`;
      row.title = `${fmt(n)} in the last ${D.calls.clock_days} days · ${fmt(a)} in the last 7`;
      row.addEventListener("click", () => select(k));
      box.appendChild(row);
    }
    const types = (D.types || []).filter((t) => !sel || t[2] === sel).slice(0, 6);
    if (types.length) {
      const p = document.createElement("p");
      p.className = "pNote";
      p.style.gridColumn = "1 / -1";
      p.innerHTML = "Most common call types" + (sel ? ` for ${catLabel(sel).toLowerCase()}` : "") +
        ": " + types.map((t) => `${t[0].toLowerCase()} (${fmt(t[1])})`).join(" · ") +
        ". Bars are the last " + D.calls.clock_days + " days; figures are the last 7, " +
        "against the 7 before them.";
      box.appendChild(p);
    }
  }

  /* --- the mosaic --------------------------------------------------------- */
  function renderHex() {
    const svg = $("pHex");
    clear(svg);
    const cells = (D.hex && D.hex.cells) || [];
    if (!cells.length) return;
    const size = D.hex.size_m;
    const pts = cells.map(([q, r]) => [1.5 * q, Math.sqrt(3) * (q / 2 + r)]);
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    const x0 = Math.min(...xs), x1 = Math.max(...xs);
    const y0 = Math.min(...ys), y1 = Math.max(...ys);
    const pad = 12;
    const k = Math.min((460 - pad * 2) / (x1 - x0 + 2), (400 - pad * 2) / (y1 - y0 + 2));
    const ox = pad + (460 - pad * 2 - (x1 - x0) * k) / 2 - x0 * k;
    const oy = pad + (400 - pad * 2 - (y1 - y0) * k) / 2 - y0 * k;
    const selIx = sel ? D.cat_order.indexOf(sel) : -1;

    const vals = cells.map((c) => (sel ? valOf(c[3], selIx) : c[2]));
    const max = Math.max(1, ...vals);

    cells.forEach((c, i) => {
      const [q, r, n, parts] = c;
      const v = sel ? valOf(parts, selIx) : n;
      const cxp = ox + pts[i][0] * k, cyp = oy + pts[i][1] * k;
      const rad = k * 0.98;
      const poly = [];
      for (let a = 0; a < 6; a++) {
        const ang = (Math.PI / 180) * (60 * a);
        poly.push(`${(cxp + rad * Math.cos(ang)).toFixed(2)},${(cyp + rad * Math.sin(ang)).toFixed(2)}`);
      }
      const domIx = parts[0][0];
      const t = Math.pow(v / max, 0.5);
      const col = sel ? catColor(sel) : catColor(D.cat_order[domIx] || "other");
      const p = el("polygon", {
        points: poly.join(" "),
        fill: v ? mix([16, 20, 31], hexToRgb(col), 0.15 + 0.85 * t) : "rgba(255,255,255,0.022)",
        opacity: v ? 1 : 0.7,
      });
      p.addEventListener("pointerenter", (e) => {
        const lines = parts.slice(0, 3).map(([ix, cnt]) =>
          `<span class="l" style="color:${catColor(D.cat_order[ix])}">■</span> ` +
          `${catLabel(D.cat_order[ix]).toLowerCase()} ${fmt(cnt)}`).join("<br>");
        showTip(e, `<b>${fmt(n)} calls here</b>${lines}` +
          `<span class="l" style="display:block;margin-top:5px">click to open on the map</span>`);
      });
      p.addEventListener("pointerleave", hideTip);
      p.addEventListener("click", () => {
        const x = size * 1.5 * q;
        const y = size * (Math.sqrt(3) / 2 * q + Math.sqrt(3) * r);
        goToMap(x / R * 180 / Math.PI,
                (2 * Math.atan(Math.exp(y / R)) - Math.PI / 2) * 180 / Math.PI, 14.5);
      });
      svg.appendChild(p);
    });
  }
  function valOf(parts, ix) {
    if (ix < 0) return 0;
    for (const [i, v] of parts) if (i === ix) return v;
    return 0;
  }

  /* --- corridors ---------------------------------------------------------- */
  function renderStreets() {
    const box = $("pStreets");
    box.innerHTML = "";
    const selIx = sel ? D.cat_order.indexOf(sel) : -1;
    let rows = (D.streets || []).map(([name, n, parts]) => ({
      name, n: sel ? valOf(parts, selIx) : n, parts,
    })).filter((r) => r.n > 0);
    rows.sort((a, b) => b.n - a.n);
    rows = rows.slice(0, 14);
    const max = Math.max(1, ...rows.map((r) => r.n));
    if (!rows.length) {
      box.innerHTML = `<p class="pNote">No corridor stands out for this category yet.</p>`;
      return;
    }
    for (const r of rows) {
      const div = document.createElement("div");
      div.className = "pStreetRow";
      const segs = sel
        ? `<i style="width:100%;background:${catColor(sel)}"></i>`
        : r.parts.map(([ix, v]) =>
            `<i style="width:${(v / r.n) * 100}%;background:${catColor(D.cat_order[ix])}"></i>`).join("");
      div.innerHTML =
        `<span class="pStreetName">${r.name.toLowerCase()}</span>` +
        `<span class="pStreetTrack"><span class="pStreetBar" ` +
        `style="width:${((r.n / max) * 100).toFixed(1)}%">${segs}</span></span>` +
        `<span class="pStreetN">${fmt(r.n)}</span>`;
      div.title = `${r.name.toLowerCase()} — ${fmt(r.n)} calls`;
      box.appendChild(div);
    }
  }

  /* --- risk assessment ----------------------------------------------------
     One statement per location, assembled from two sources with opposite gaps.
     The DAY comes from eight years of reported offenses at that address; the
     HOUR comes from that location's own dispatch calls when there are enough of
     them, and from the citywide clock for the category when there are not. Each
     card says which, because they are not the same strength of claim. */
  const DOWFULL = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
                   "Saturday", "Sunday"];

  function riskFor(place) {
    const H = D.hotspots;
    // which offense is this place's story? the filter wins when it applies
    let cat = null;
    if (sel && place.cat_dow && place.cat_dow[sel]) cat = sel;
    if (!cat) {
      const byCat = Object.entries(place.by_cat || {});
      if (!byCat.length) return null;
      cat = byCat.sort((a, b) => b[1] - a[1])[0][0];
    }
    const n = (place.by_cat || {})[cat] || 0;
    const dow = (place.cat_dow || {})[cat] || place.dow;
    const total = sum(dow);
    if (!total) return null;
    let peakDow = 0;
    for (let i = 1; i < 7; i++) if (dow[i] > dow[peakDow]) peakDow = i;
    const lift = dow[peakDow] / (total / 7);

    const ownClock = !!place.hours;
    const hours = ownClock ? place.hours : (H.city_hours || {})[cat] || null;
    const peakHr = ownClock ? place.peak
      : (H.city_peak || {})[cat] != null ? H.city_peak[cat] : null;
    return { cat, n, dow, peakDow, lift, hours, peakHr, ownClock };
  }

  function riskCard(p) {
    const r = riskFor(p);
    if (!r) return null;
    const H = D.hotspots;
    const recent = p.recent || { n: 0, per_week: 0, by_cat: {} };
    const isRecent = p.src === "recent";
    const col = catColor(r.cat);
    const where = p.name || (p.addr || "").toLowerCase() || "this block";
    const card = document.createElement("div");
    card.className = "pRiskCard" + (isRecent ? " isNow" : "");
    card.style.setProperty("--c", col);

    const window2 = r.peakHr == null ? null
      : `${hourLabel(r.peakHr)} and ${hourLabel((r.peakHr + 2) % 24)}`;
    card.innerHTML =
      `<div class="pRiskHead">Higher risk of <b>${catLabel(r.cat).toLowerCase()}</b> ` +
      `at <b>${where}</b> on <span class="when">${DOWFULL[r.peakDow]}</span>` +
      (window2 ? `, most likely between <span class="when">${window2}</span>` : "") +
      `.</div>` +
      (p.name && p.addr ? `<div class="pRiskWhere">${p.addr.toLowerCase()}</div>` : "");

    const stat = document.createElement("div");
    stat.className = "pRiskStat";
    stat.innerHTML = isRecent
      ? `<b>${fmt(r.n)}</b> ${catLabel(r.cat).toLowerCase()} calls for service · ` +
        `about <b>${recent.per_week}</b> calls a week here · ` +
        `<b>${r.lift.toFixed(1)}×</b> more likely on a ${DOWFULL[r.peakDow]} ` +
        `than an average day here.`
      : `<b>${fmt(r.n)}</b> ${catLabel(r.cat).toLowerCase()} reports · ` +
        `<b>${p.per_year}</b> a year across all offenses · ` +
        `<b>${r.lift.toFixed(1)}×</b> more likely on a ${DOWFULL[r.peakDow]} than an ` +
        `average day here.`;
    card.appendChild(stat);

    const charts = document.createElement("div");
    charts.className = "pRiskCharts";
    charts.appendChild(dowChart(r, col));
    if (r.hours) charts.appendChild(hourChart(r, col));
    card.appendChild(charts);

    // what the place looks like currently, alongside its longer record
    if (!isRecent) {
      const top = Object.entries(recent.by_cat || {})
        .sort((a, b) => b[1] - a[1]).slice(0, 2)
        .map(([k, v]) => `${catLabel(k).toLowerCase()} ${v}`).join(", ");
      const now = document.createElement("div");
      now.className = "pRiskNow";
      now.innerHTML = recent.n
        ? `<b>Currently:</b> ${recent.per_week} calls for service a week here` +
          (top ? ` — ${top}.` : ".")
        : `<b>Currently:</b> no calls for service here.`;
      card.appendChild(now);
    }

    const src = document.createElement("div");
    src.className = "pRiskSrc";
    src.textContent = isRecent
      ? `Built from current calls for service at this spot — a live pattern rather ` +
        `than an established one.`
      : r.ownClock
        ? `Hour measured here, from ${fmt(p.dsp_n)} dispatch calls at this location.`
        : `Hour is the citywide pattern for ${catLabel(r.cat).toLowerCase()} — not ` +
          `enough calls at this address yet to time it here.`;
    card.appendChild(src);

    const foot = document.createElement("div");
    foot.className = "pRiskFoot";
    const b = document.createElement("button");
    b.textContent = "see it on the map →";
    b.addEventListener("click", () => zoomTo(p));
    foot.appendChild(b);
    for (const rep of (p.reports || [])) {
      if (!rep.url) continue;
      const a = document.createElement("a");
      a.href = rep.url + (rep.pdf_page ? "#page=" + rep.pdf_page : "");
      a.target = "_blank";
      a.rel = "noopener";
      a.textContent = `report ${rep.date || rep.no} ↗`;
      foot.appendChild(a);
    }
    card.appendChild(foot);
    return card;
  }

  function renderRisk() {
    const box = $("pRisk");
    box.innerHTML = "";
    const H = D.hotspots;
    if (!H || !(H.places || []).length) {
      $("pRiskNote").textContent = "";
      box.innerHTML = `<p class="pCaseNone">No location has enough history yet.</p>`;
      return;
    }
    let list = H.places.slice();
    if (sel) list = list.filter((p) => (p.cat_dow || {})[sel]);

    // One ranking, both records in it. Raw counts cannot be compared across the
    // two — a place on the long record has years behind it, a current one has
    // weeks — so rank on an annualised rate, which puts them on the same axis.
    const ratio = H.call_to_offense || 1;
    const yearRate = (p) => {
      if (p.src === "recent") {
        const n = sel ? (p.by_cat[sel] || 0) : sum(Object.values(p.by_cat));
        // scaled into offense-equivalent terms, or every call-based entry would
        // outrank every offense-based one purely because calls are commoner
        return n * (365 / Math.max(1, H.archive_days)) / ratio;
      }
      const n = sel ? (p.by_cat[sel] || 0) : p.n;
      const span = Math.max(1, (p.last || 0) - (p.first || 0) + 1);
      return n / span;
    };
    list.sort((a, b) => yearRate(b) - yearRate(a));

    $("pRiskNote").innerHTML =
      `Locations with enough on record to say something: at least ` +
      `<b>${H.min_events}</b> reported offenses, or <b>${H.min_recent}</b> ` +
      `reportable calls for service where the offense record is thin` +
      (sel ? `, filtered to ${catLabel(sel).toLowerCase()}` : "") +
      `. Ranked by rate, so a place that is busy now sits alongside one with a ` +
      `long history. The day is that location's own pattern; the hour is measured ` +
      `there once there are at least ${H.min_place_hours} calls to measure, and is ` +
      `the citywide pattern for that offense until then.`;

    if (!list.length) {
      box.innerHTML = `<p class="pCaseNone">Nothing has enough ` +
        `${sel ? catLabel(sel).toLowerCase() : "activity"} on record to say ` +
        `anything useful.</p>`;
      return;
    }

    for (const p of list.slice(0, 15)) {
      const card = riskCard(p);
      if (card) box.appendChild(card);
    }
  }

  function dowChart(r, col) {
    const svg = el("svg", { viewBox: "0 0 108 46" });
    const max = Math.max(1, ...r.dow);
    const base = hexToRgb(col);
    for (let i = 0; i < 7; i++) {
      const h = (r.dow[i] / max) * 30;
      svg.appendChild(el("rect", {
        x: i * 15.4 + 1.5, y: (34 - h).toFixed(1), width: 12,
        height: Math.max(1, h).toFixed(1), rx: 2,
        fill: i === r.peakDow ? col : mix([26, 32, 48], base, 0.35),
      }));
      svg.appendChild(el("text", {
        x: i * 15.4 + 7.5, y: 43, "text-anchor": "middle",
        "font-weight": i === r.peakDow ? "700" : "400",
      }, DOW[i][0]));
    }
    return svg;
  }

  function hourChart(r, col) {
    const svg = el("svg", { viewBox: "0 0 216 46" });
    const max = Math.max(1, ...r.hours);
    const base = hexToRgb(col);
    for (let h = 0; h < 24; h++) {
      const inWin = r.peakHr != null &&
        (h === r.peakHr || h === (r.peakHr + 1) % 24);
      const bh = (r.hours[h] / max) * 30;
      svg.appendChild(el("rect", {
        x: h * 9 + 0.8, y: (34 - bh).toFixed(1), width: 7.4,
        height: Math.max(1, bh).toFixed(1), rx: 1.5,
        fill: inWin ? col : mix([26, 32, 48], base, 0.3),
      }));
    }
    for (const h of [0, 6, 12, 18]) {
      svg.appendChild(el("text", { x: h * 9 + 4.5, y: 43, "text-anchor": "middle" },
        h === 0 ? "12a" : h === 12 ? "12p" : h < 12 ? h + "a" : (h - 12) + "p"));
    }
    return svg;
  }

  /* Hand-off to the map: fly in to building level and switch the dispatch
     overlay on so the incidents are actually visible when you land. */
  function zoomTo(p) {
    try {
      const secs = $("dispatchSec");
      if (secs) secs.open = true;
      const on = $("dspOn");
      if (on && !on.checked) {
        on.checked = true;
        on.dispatchEvent(new Event("change"));
      }
      const all = document.querySelector('input[name="dspMode"][value="all"]');
      if (all && !all.checked) {
        all.checked = true;
        all.dispatchEvent(new Event("change"));
      }
    } catch (e) { /* the overlay is a bonus, the fly-to is the point */ }
    goToMap(p.lon, p.lat, 17);
  }

  /* --- case files --------------------------------------------------------- */
  function renderCases() {
    const box = $("pCases");
    box.innerHTML = "";
    const rep = D.reports || {};
    const labels = rep.tag_labels || {};
    let cases = rep.cases || [];
    if (sel) cases = cases.filter((c) => c.cat === sel);
    $("pCasesNote").innerHTML = rep.collected
      ? `Every weekday the city posts a PDF of complete incident reports and takes the ` +
        `old ones down within about a week, so these are collected as they appear — ` +
        `<b>${fmt(rep.incidents)}</b> incidents from <b>${fmt(rep.collected)}</b> reports so far. ` +
        `Narratives name victims and witnesses; they are read to work out what happened ` +
        `and then discarded, so what you see below is the incident, not the people in it.`
      : "No daily reports collected yet.";
    if (!cases.length) {
      box.innerHTML = `<p class="pCaseNone">No case files${sel ? " in this category" : ""} yet.</p>`;
      return;
    }
    for (const c of cases.slice(0, 30)) {
      const col = catColor(c.cat || "other");
      const div = document.createElement("div");
      div.className = "pCase";
      div.style.setProperty("--c", col);
      const when = c.dt
        ? new Date(c.dt).toLocaleString(undefined,
            { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })
        : (c.date ? `about ${dayLabel(c.date)}` : "date unclear");
      const title = c.call_type_label || (c.offenses && c.offenses[0]) || catLabel(c.cat || "other");
      div.innerHTML =
        `<div class="pCaseTop"><span class="pCaseWhen">${when}</span>` +
        `<span class="pCaseNo">${c.no || ""}</span></div>` +
        `<div class="pCaseTitle">${title}</div>` +
        (c.offenses && c.offenses.length
          ? `<div class="pCaseOff">${c.offenses.slice(0, 4).map((o) =>
              `<span>${o.toLowerCase()}</span>`).join("")}</div>` : "") +
        (c.tags && c.tags.length
          ? `<div class="pCaseTags">${c.tags.map((t) =>
              `<span>${labels[t] || t}</span>`).join("")}</div>` : "") +
        (c.loc ? `<div class="pCaseWhere">${c.loc.toLowerCase()}` +
                 (c.district ? ` · district ${c.district}` : "") + `</div>` : "");
      const foot = document.createElement("div");
      foot.className = "pCaseFoot";
      if (c.lon != null) {
        const b = document.createElement("button");
        b.textContent = "see on map";
        b.addEventListener("click", () => goToMap(c.lon, c.lat, 16));
        foot.appendChild(b);
      }
      if (c.url) {
        const a = document.createElement("a");
        a.href = c.url + (c.pdf_page ? "#page=" + c.pdf_page : "");
        a.target = "_blank";
        a.rel = "noopener";
        a.textContent = "city's report ↗";
        foot.appendChild(a);
      }
      if (foot.childNodes.length) div.appendChild(foot);
      box.appendChild(div);
    }
  }

  /* --- the long view ------------------------------------------------------ */
  function renderHistory() {
    const box = $("pHistory");
    box.innerHTML = "";
    const h = D.history;
    if (!h) { box.innerHTML = `<p class="pNote">No historical export loaded.</p>`; return; }
    const cats = Object.keys(h.by_month)
      .filter((k) => !sel || k === sel)
      .sort((a, b) => sum(h.by_month[b]) - sum(h.by_month[a]));
    if (!cats.length) {
      box.innerHTML = `<p class="pNote">The 2017–${h.years[h.years.length - 1]} ` +
        `export only covers reported burglary, robbery, assault and theft, so ` +
        `there is no long view for ${catLabel(sel).toLowerCase()}.</p>`;
      return;
    }
    const fy = h.full_years, fyIx = h.years.map((y, i) => [y, i])
      .filter(([y]) => fy.includes(y)).map(([, i]) => i);
    for (const k of cats) {
      const months = h.by_month[k];
      const yearly = fyIx.map((i) => h.by_year[k][i]);
      const mMax = Math.max(1, ...months), mMin = Math.min(...months);
      const cell = document.createElement("div");
      cell.className = "pHistCell";
      const hi = months.indexOf(mMax), lo = months.indexOf(mMin);
      const chg = yearly.length > 1 && yearly[0]
        ? Math.round(((yearly[yearly.length - 1] - yearly[0]) / yearly[0]) * 100) : null;
      cell.innerHTML =
        `<h3 style="color:${catColor(k)}">${catLabel(k)}</h3>` +
        `<div class="sub">busiest in ${MONTHS[hi]}, quietest in ${MONTHS[lo]}` +
        (chg == null ? "" : ` · <span class="${chg > 0 ? "up" : "down"}">${chg > 0 ? "+" : ""}${chg}%</span> ` +
          `${fy[0]}→${fy[fy.length - 1]}`) + `</div>`;
      const svg = el("svg", { viewBox: "0 0 240 96" });
      const col = hexToRgb(catColor(k));
      // bars start from a suppressed baseline: month-to-month swings are only a
      // few per cent, and a true zero baseline flattens them into one grey slab
      const floor = mMin * 0.82, span = Math.max(1, mMax - floor);
      months.forEach((v, i) => {
        const bh = ((v - floor) / span) * 62;
        svg.appendChild(el("rect", {
          x: (i * 20 + 1).toFixed(1), y: (74 - bh).toFixed(1), width: 18,
          height: Math.max(1, bh).toFixed(1), rx: 2,
          fill: mix(col.map((c) => c * 0.32), col, (v - mMin) / (mMax - mMin || 1)),
        }));
        if (i % 3 === 0) {
          svg.appendChild(el("text", { x: i * 20 + 10, y: 88, "text-anchor": "middle" },
            MONTHS[i]));
        }
      });
      cell.appendChild(svg);
      box.appendChild(cell);
    }
    const p = document.createElement("p");
    p.className = "pNote";
    p.innerHTML = `Month-of-year shape of <b>${fmt(h.total)}</b> reported offenses ` +
      `across the complete years ${fy[0]}–${fy[fy.length - 1]} of LRPD's published ` +
      `index-offense export. Rape is excluded because LRPD suppresses those locations.`;
    box.appendChild(p);
  }

  /* -------------------------------------------------------------- bootstrap */
  function init() {
    for (const b of document.querySelectorAll(".tabBtn")) {
      b.addEventListener("click", () => showView(b.dataset.view));
    }
    // second way in: on a phone the panel fills the screen, so a floating pill
    // is easy to read past. The panel's own footer is where people look.
    const link = $("pulseLink");
    if (link) {
      link.addEventListener("click", (e) => { e.preventDefault(); showView("pulse"); });
    }
    $("pFilterClear").addEventListener("click", () => select(sel));
    addEventListener("scroll", hideTip, true);
    if (location.hash === "#pulse") showView("pulse");
    else if (location.hash === "#watch") showView("watch");
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
