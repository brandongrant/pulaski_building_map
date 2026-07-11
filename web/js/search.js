/* Pulaski County Building Map — owner index + owner/address search */
import { $, fmt, fmtUSD, esc, normAddrJS } from "./util.js";
import { map } from "./map.js";

export const own = { loaded: false, loading: null, cities: [], owners: [], namesN: [], byAddr: null, byParcel: null, subs: [] };

export function ownersLoad() {
  if (own.loading) return own.loading;
  const sb = $("searchBox");
  sb.placeholder = "Loading owner index…";
  own.loading = fetch("data/owners.json")
    .then((r) => { if (!r.ok) throw new Error("owners.json missing"); return r.json(); })
    .then((d) => {
      own.cities = d.cities;
      own.subs = d.subs || [];
      own.owners = d.owners;
      own.namesN = new Array(d.owners.length);
      own.byAddr = new Map();
      own.byParcel = new Map();
      d.owners.forEach(([name, props], oi) => {
        own.namesN[oi] = normAddrJS(name);
        for (const pr of props) {
          if (pr[5]) own.byParcel.set(pr[5], [oi, pr]);
          if (!pr[0]) continue;
          const key = normAddrJS(pr[0]) + "|" + (pr[1] >= 0 ? normAddrJS(d.cities[pr[1]]) : "");
          const a = own.byAddr.get(key);
          if (a) a.push(oi); else own.byAddr.set(key, [oi]);
        }
      });
      own.loaded = true;
      sb.placeholder = `Search ${fmt.format(d.count)} owners & addresses…`;
      return true;
    })
    .catch(() => { sb.placeholder = "Search index unavailable"; return false; });
  return own.loading;
}

export function ownerAtAddr(p) {
  if (!own.loaded || !p.addr) return null;
  const key = normAddrJS(p.addr) + "|" + normAddrJS(p.city || "");
  const ois = own.byAddr.get(key);
  if (!ois) return null;
  const names = [...new Set(ois.map((i) => own.owners[i][0]).filter(Boolean))];
  return names.length ? names : null;
}

export function ownerLinkHTML(name) {
  const enc = encodeURIComponent(name).replace(/'/g, "%27");
  return `<span class="owner-link" onclick="__ownerSearch('${enc}')" ` +
         `title="Show every property of this owner">${esc(name)}</span>`;
}

function parcelFromRow(oi, pr) {
  const sub = pr[6] ? (own.subs[pr[6]] || "") : "";
  const blc = pr[8] && pr[8] !== "0" ? pr[8] : "";
  return { id: pr[5] || "", owner: own.owners[oi][0], value: pr[4] || 0,
           sub, lot: pr[7] || "", blc };
}

function parcelAtAddr(p) {
  if (!own.loaded || !p.addr) return null;
  const key = normAddrJS(p.addr) + "|" + normAddrJS(p.city || "");
  const ois = own.byAddr.get(key);
  if (!ois) return null;
  for (const oi of ois) {
    const props = own.owners[oi][1] || [];
    for (const pr of props) {
      const city = pr[1] >= 0 ? own.cities[pr[1]] : "";
      if (normAddrJS(pr[0]) === normAddrJS(p.addr) && normAddrJS(city) === normAddrJS(p.city || "")) {
        return parcelFromRow(oi, pr);
      }
    }
  }
  return null;
}

// Popup identity resolution: prefer the stable parcel id baked into high-zoom
// tiles (ID-001) over normalized-address matching. Address stays as fallback
// for one release; counters measure coverage so it can be retired once pid
// resolution is proven (roadmap §6.2 migration behavior).
export const parcelResolveStats = { pid: 0, addr: 0, miss: 0 };

export function parcelForFeature(p) {
  if (own.loaded && p.pid && own.byParcel) {
    const hit = own.byParcel.get(p.pid);
    if (hit) {
      parcelResolveStats.pid++;
      return parcelFromRow(hit[0], hit[1]);
    }
  }
  const viaAddr = parcelAtAddr(p);
  parcelResolveStats[viaAddr ? "addr" : "miss"]++;
  return viaAddr;
}

export function searchRun(qRaw) {
  const box = $("searchResults");
  const q = normAddrJS(qRaw);                                     // for names
  const qU = String(qRaw || "").toUpperCase().replace(/\s+/g, " ").trim(); // for addresses
  if (q.length < 2) { box.hidden = true; box.innerHTML = ""; return; }
  const MAX = 8;
  const oStart = [], oIn = [], aStart = [], aIn = [];
  for (let oi = 0; oi < own.owners.length; oi++) {
    const nn = own.namesN[oi];
    if (nn) {
      const i = nn.indexOf(q);
      if (i === 0) { if (oStart.length < MAX) oStart.push(oi); }
      else if (i > 0 && oIn.length < MAX) oIn.push(oi);
    }
    const props = own.owners[oi][1];
    for (let pi = 0; pi < props.length; pi++) {
      const ad = props[pi][0];
      if (!ad) continue;
      const i = ad.indexOf(qU);
      if (i === 0) { if (aStart.length < MAX) aStart.push([oi, pi]); }
      else if (i > 0 && aIn.length < MAX) aIn.push([oi, pi]);
    }
    if (oStart.length >= MAX && aStart.length >= MAX) break;
  }
  const owners = oStart.concat(oIn).slice(0, MAX);
  const addrs = aStart.concat(aIn).slice(0, MAX);
  let html = "";
  if (owners.length) {
    html += `<div class="sr-head">Owners</div>`;
    for (const oi of owners) {
      const [name, props] = own.owners[oi];
      const n = props.length;
      let tot = 0;
      for (const pr of props) tot += pr[4] || 0;
      html += `<div class="sr-item" data-k="o:${oi}"><b>${esc(name)}</b>` +
        `<span class="sr-sub">${n} propert${n === 1 ? "y" : "ies"}` +
        `${tot > 0 ? " · " + fmtUSD.format(tot) + " total value" : ""}</span></div>`;
    }
  }
  if (addrs.length) {
    html += `<div class="sr-head">Addresses</div>`;
    for (const [oi, pi] of addrs) {
      const [name, props] = own.owners[oi];
      const pr = props[pi];
      const city = pr[1] >= 0 ? own.cities[pr[1]] : "";
      html += `<div class="sr-item" data-k="a:${oi}:${pi}"><b>${esc(pr[0])}${city ? ", " + esc(city) : ""}</b>` +
        `<span class="sr-sub">${name ? esc(name) : "—"}</span></div>`;
    }
  }
  if (!html) html = `<div class="sr-none">No owner or address matches "${esc(qRaw.trim())}"</div>`;
  box.innerHTML = html;
  box.hidden = false;
}

function hitFeatures(list) {
  return list.map((h) => ({
    type: "Feature",
    geometry: { type: "Point", coordinates: [h.lon, h.lat] },
    properties: { a: h.addr, c: h.city, o: h.owner, v: h.val, pid: h.parcelId || "" },
  }));
}

function setHits(list, label) {
  const feats = hitFeatures(list.filter((h) => h.lon && h.lat));
  const apply = () => map.getSource("hits").setData({ type: "FeatureCollection", features: feats });
  if (map.getSource("hits")) apply(); else map.once("load", apply);
  if (feats.length) {
    if (feats.length === 1) {
      map.flyTo({ center: feats[0].geometry.coordinates, zoom: Math.max(map.getZoom(), 16.8), duration: 1400 });
    } else {
      let minX = 180, minY = 90, maxX = -180, maxY = -90;
      for (const f of feats) {
        const [x, y] = f.geometry.coordinates;
        if (x < minX) minX = x; if (x > maxX) maxX = x;
        if (y < minY) minY = y; if (y > maxY) maxY = y;
      }
      const panelOpen = window.innerWidth > 640 && !$("panel").classList.contains("hidden");
      const pad = { top: 70, bottom: 70, right: 70, left: panelOpen ? 330 : 70 };
      try {
        map.fitBounds([[minX, minY], [maxX, maxY]], { padding: pad, maxZoom: 16.5, duration: 1400 });
      } catch (e) {
        // padding can exceed the map size (narrow windows, hidden tabs) — retry bare
        map.fitBounds([[minX, minY], [maxX, maxY]], { maxZoom: 16.5, duration: 1400 });
      }
    }
  }
  $("searchHitsTxt").textContent = label;
  $("searchHits").hidden = false;
}

function clearHits() {
  if (map.getSource("hits")) map.getSource("hits").setData({ type: "FeatureCollection", features: [] });
  $("searchHits").hidden = true;
  $("searchResults").hidden = true;
}

export function selectResult(kind, oi, pi) {
  const [name, props] = own.owners[oi];
  const toHit = (pr) => ({ addr: pr[0], city: pr[1] >= 0 ? own.cities[pr[1]] : "",
                           lon: pr[2], lat: pr[3], val: pr[4], parcelId: pr[5] || "", owner: name });
  if (kind === "o") {
    setHits(props.map(toHit), `${name} — ${props.length} propert${props.length === 1 ? "y" : "ies"}`);
  } else {
    const h = toHit(props[pi]);
    setHits([h], h.addr ? h.addr + (h.city ? ", " + h.city : "") : name);
  }
  $("searchResults").hidden = true;
  // on phones the panel covers the map — tuck it away so the result is visible
  if (window.matchMedia("(max-width: 640px)").matches) {
    $("panel").classList.add("hidden");
    $("panelToggle").classList.add("show");
  }
}

window.__ownerSearch = (enc) => {
  const name = decodeURIComponent(enc);
  $("searchBox").value = name;
  $("panel").classList.remove("hidden");
  $("panelToggle").classList.remove("show");
  ownersLoad().then((ok) => {
    if (!ok) return;
    const oi = own.owners.findIndex((o) => o[0] === name);
    if (oi >= 0) selectResult("o", oi);
  });
};

export function initSearch() {
  const sb = $("searchBox"), box = $("searchResults");
  let t = null, sel = -1;
  const items = () => [...box.querySelectorAll(".sr-item")];
  const run = () => {
    sel = -1;
    if (!own.loaded) { ownersLoad().then((ok) => { if (ok && sb.value) searchRun(sb.value); }); return; }
    searchRun(sb.value);
  };
  sb.oninput = () => { clearTimeout(t); t = setTimeout(run, 140); };
  sb.onfocus = () => { ownersLoad(); if (sb.value) run(); };
  const pick = (el) => {
    const k = el.dataset.k.split(":");
    selectResult(k[0], Number(k[1]), k[2] !== undefined ? Number(k[2]) : undefined);
  };
  sb.onkeydown = (e) => {
    const it = items();
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      if (!it.length) return;
      e.preventDefault();
      sel = e.key === "ArrowDown" ? Math.min(sel + 1, it.length - 1) : Math.max(sel - 1, 0);
      it.forEach((x, i) => x.classList.toggle("sel", i === sel));
      it[sel].scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter") {
      if (it.length) pick(it[Math.max(sel, 0)]);
      sb.blur();
    } else if (e.key === "Escape") {
      box.hidden = true;
    }
  };
  box.onclick = (e) => {
    const el = e.target.closest(".sr-item");
    if (el) pick(el);
  };
  document.addEventListener("click", (e) => {
    if (!$("searchSec").contains(e.target)) box.hidden = true;
  });
  $("searchHitsClear").onclick = () => { clearHits(); sb.value = ""; };
  // warm the index in the background once things settle (not on data-saver)
  if (!(navigator.connection && navigator.connection.saveData)) {
    setTimeout(ownersLoad, 3500);
  }
}
