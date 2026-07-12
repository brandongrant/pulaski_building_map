/* Pulaski County Building Map — building tooltip, click popups, record links */
import { $, fmt, fmtUSD, esc } from "./util.js";
import { CATS } from "./config.js";
import { map } from "./map.js";
import { ownerAtAddr, ownerLinkHTML, parcelForFeature } from "./search.js";
import { PM_CATS, permitTimeline } from "./overlays/permits.js";
import { DEED_TYPES, deedsForBuilding, deedsTimeline } from "./overlays/deeds.js";
import { sr311Timeline, srDate } from "./overlays/requests311.js";
import { profileLinkHTML } from "./profile.js";
import {
  PULASKI_DEEDS_ACCEPT_URL, parcelIdForURL, arCountyParcelURL, treasurerURL,
  deedDocLink, deedOwnerLink, deedHistoryAvailable, fetchDeedHistory,
} from "./api.js";

function recordLinksLegacy() {
  return `<div class="tt-veh pp-links">Public records: ` +
    `<a href="${PULASKI_DEEDS_ACCEPT_URL}" target="_blank" rel="noopener" ` +
    `title="Pulaski County recorded documents — deeds, mortgages, liens (1994+)">deeds</a> · ` +
    `<a href="https://www.arcountydata.com/county.asp?county=pulaski&amp;directlogin=true" target="_blank" rel="noopener" ` +
    `title="ARCountyData Pulaski property records">parcel</a> · ` +
    `<a href="https://pulaskicountyassessor.net/" target="_blank" rel="noopener" ` +
    `title="Pulaski County Assessor">assessor</a> · ` +
    `<a href="https://public.pulaskicountytreasurer.net/" target="_blank" rel="noopener" ` +
    `title="Pulaski County Treasurer property tax records">taxes</a></div>`;
}

export function recordLinks(ctx = {}) {
  const docs = Array.isArray(ctx.docs) ? ctx.docs : null;
  const deedDoc = docs && docs.find((p) => p.n);
  const parcelId = parcelIdForURL(ctx.parcelId || (ctx.parcel && ctx.parcel.id));
  const owner = ctx.owner || (ctx.parcel && ctx.parcel.owner) || "";
  const address = ctx.address || "";
  const deedsLink = deedDoc
    ? deedDocLink(deedDoc.n, "deeds")
    : owner ? deedOwnerLink(owner, "deeds")
    : `<a href="${PULASKI_DEEDS_ACCEPT_URL}" target="_blank" rel="noopener" ` +
      `title="Pulaski County recorded documents - deeds, mortgages, liens (1994+)">deeds</a>`;
  const parcelLink = parcelId
    ? `<a href="${arCountyParcelURL(parcelId)}" target="_blank" rel="noopener" ` +
      `title="Open this parcel's ARCountyData property record">parcel</a>`
    : `<a href="https://www.arcountydata.com/county.asp?county=pulaski&amp;directlogin=true" target="_blank" rel="noopener" ` +
      `title="ARCountyData Pulaski property records">parcel</a>`;
  const assessorLink = parcelId
    ? `<a href="${arCountyParcelURL(parcelId)}" target="_blank" rel="noopener" ` +
      `title="Open the assessor-sponsored ARCountyData property record">assessor</a>`
    : `<a href="https://pulaskicountyassessor.net/" target="_blank" rel="noopener" ` +
      `title="Pulaski County Assessor">assessor</a>`;
  const taxLink = parcelId || address
    ? `<a href="${treasurerURL(parcelId, address)}" target="_blank" rel="noopener" ` +
      `title="Open Pulaski County Treasurer tax inquiry for this parcel">taxes</a>`
    : `<a href="https://public.pulaskicountytreasurer.net/mobile/pulaski/" target="_blank" rel="noopener" ` +
      `title="Pulaski County Treasurer property tax records">taxes</a>`;
  return `<div class="tt-veh pp-links">Public records: ` +
    `${deedsLink} &middot; ${parcelLink} &middot; ${assessorLink} &middot; ${taxLink}</div>`;
}

/* ------------------------------------------------- hover + click */
export function featHTML(p, compactOnly) {
  const cat = CATS[p.cat] || CATS[0];
  const yr = p.yr > 0 ? p.yr : "unknown";
  let rows = "";
  const add = (k, v) => { rows += `<span class="k">${k}</span><span>${v}</span>`; };
  if (!compactOnly) {
    const names = ownerAtAddr(p);
    if (names) {
      add("Owner", ownerLinkHTML(names[0]) +
        (names.length > 1 ? ` <span style="color:var(--txt-dim)">+${names.length - 1} more</span>` : ""));
    }
  }
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

export function wireHover() {
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
    // vehicle-search results sit above every other map layer
    if (map.getLayer("veh-point")) {
      const vf = map.queryRenderedFeatures(e.point, { layers: ["veh-cluster", "veh-point"] });
      if (vf.length) {
        if (popup) { popup.remove(); popup = null; }
        const f = vf[0];
        if (f.layer.id === "veh-cluster") {
          map.getSource("veh-src").getClusterExpansionZoom(f.properties.cluster_id, (err, z) => {
            if (!err) map.easeTo({ center: f.geometry.coordinates, zoom: Math.min(z, 18), duration: 600 });
          });
        } else {
          const p = f.properties;
          const lines = String(p.v || "").split("\n").filter(Boolean).map(esc).join("<br>");
          popup = new maplibregl.Popup({ closeButton: true, maxWidth: "300px" })
            .setLngLat(f.geometry.coordinates)
            .setHTML(`<div class="pp-addr">${esc(p.a || "Vehicle location")}${p.c ? ", " + esc(p.c) : ""}</div>` +
              `<div class="tt-veh"><b>${p.n} matching vehicle${p.n > 1 ? "s" : ""} at this address</b><br>${lines}</div>`)
            .addTo(map);
        }
        return;
      }
    }
    // overlay features sit above buildings — they win the click
    const ovLayers = ["hit-ring", "pm-pts", "sr-pts", "deed-pts", "dsp-pts", "dsp-grid"].filter((l) => map.getLayer(l));
    if (ovLayers.length) {
      const df = map.queryRenderedFeatures(e.point, { layers: ovLayers });
      if (df.length) {
        if (popup) popup.remove();
        const p = df[0].properties;
        let html;
        if (df[0].layer.id === "hit-ring") {
          const where = p.a ? esc(p.a) + (p.c ? ", " + esc(p.c) : "") : "(no situs address)";
          const docs = p.a ? deedsForBuilding({ addr: p.a }) : null;
          html = `<div class="pp-addr">${where}</div><div class="pp-grid">` +
            (p.o ? `<span class="k">Owner</span><span>${ownerLinkHTML(p.o)}</span>` : "") +
            (p.v > 0 ? `<span class="k">Parcel value</span><span>${fmtUSD.format(p.v)}</span>` : "") +
            `</div>` + recordLinks({ parcelId: p.pid, address: p.a, owner: p.o, docs }) +
            profileLinkHTML(p.pid) +
            `<div class="tt-veh">Owner per county parcel roll · unofficial</div>`;
        } else if (df[0].layer.id === "pm-pts") {
          const d = String(p.d);
          html = `<div class="pp-addr">${PM_CATS[p.t].label} permit</div><div class="pp-grid">` +
            `<span class="k">Where</span><span>${p.a}</span>` +
            `<span class="k">Issued</span><span>${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6, 8)}</span>` +
            `<span class="k">Status</span><span>${{ O: "Open", C: "Closed", W: "Stop work" }[p.s] || "—"}</span>` +
            (p.v ? `<span class="k">Value</span><span>${fmtUSD.format(p.v)}</span>` : "") +
            (p.sf ? `<span class="k">Sq ft</span><span>${fmt.format(p.sf)}</span>` : "") +
            `<span class="k">Permit #</span><span>${p.n}</span></div>` +
            (p.ds ? `<div class="tt-veh">${esc(p.ds)}</div>` : "") +
            `<div class="tt-veh">City of Little Rock permit record · unofficial</div>`;
        } else if (df[0].layer.id === "sr-pts") {
          html = `<div class="pp-addr">${esc(p.ty)}</div><div class="pp-grid">` +
            `<span class="k">Where</span><span>${esc(p.a || "")}${p.c ? ", " + esc(p.c) : ""}</span>` +
            (p.o ? `<span class="k">Opened</span><span>${srDate(p.o)}</span>` : "") +
            (p.cl ? `<span class="k">Closed</span><span>${srDate(p.cl)}</span>`
                  : `<span class="k">Status</span><span>${esc(p.sd || "—")}</span>`) +
            `<span class="k">Updated</span><span>${srDate(p.u)}</span>` +
            (p.ch ? `<span class="k">Via</span><span>${esc(p.ch)}</span>` : "") +
            `<span class="k">Request #</span><span>${esc(p.n)}</span></div>` +
            `<div class="tt-veh">City of Little Rock 311 service request · unofficial</div>`;
        } else if (df[0].layer.id === "deed-pts") {
          const d = String(p.d);
          html = `<div class="pp-addr">${esc(p.dt || DEED_TYPES[p.t]?.label || "Deed activity")}</div><div class="pp-grid">` +
            `<span class="k">Where</span><span>${esc(p.a || "Matched location")}</span>` +
            `<span class="k">Recorded</span><span>${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6, 8)}</span>` +
            `<span class="k">Document #</span><span>${p.n ? deedDocLink(p.n, p.n) : ""}</span>` +
            `<span class="k">Match</span><span>${esc(p.mq || "geocoded")}</span></div>` +
            `<div class="tt-veh">Pulaski County deed index match · party names omitted · unofficial</div>`;
        } else if (df[0].layer.id === "dsp-pts") {
          html = `<div class="pp-addr">${p.t}</div><div class="pp-grid">` +
            `<span class="k">Where</span><span>${p.loc}</span>` +
            `<span class="k">When</span><span>${new Date(p.ts).toLocaleString()}</span>` +
            `<span class="k">Category</span><span>${p.c}</span></div>` +
            `<div class="tt-veh">Call-for-service record, delayed 30 min–8 hr. Not a confirmed crime or report.</div>`;
        } else {
          html = `<div class="pp-addr">${p.n} dispatches · last 30 days</div>` +
            `<div class="tt-veh">${p.top || ""}<br>≈500 ft grid cell</div>`;
        }
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
    const docs = deedsForBuilding(fs[0].properties);
    const parcel = parcelForFeature(fs[0].properties);
    // when the deeds proxy is configured, its live parcel history replaces the
    // sparse local-archive timeline
    const useWorker = deedHistoryAvailable(parcel);
    popup = new maplibregl.Popup({ closeButton: true, maxWidth: "310px" })
      .setLngLat(e.lngLat)
      .setHTML(`<div class="pp-addr">${addr}</div><div class="pp-grid">${rows}</div>` +
               veh + permitTimeline(fs[0].properties) + sr311Timeline(fs[0].properties) +
               (useWorker ? deedHistorySection(parcel) : deedsTimeline(fs[0].properties, docs)) +
               recordLinks({ parcel, address: fs[0].properties.addr, docs }) +
               profileLinkHTML(fs[0].properties.pid || (parcel && parcel.id)))
      .addTo(map);
    if (useWorker) loadDeedHistory(popup, parcel);
  });
}

/* -------------------------------- live deed history — popup rendering */
// placeholder rendered synchronously in the popup; filled by loadDeedHistory
export function deedHistorySection(parcel) {
  if (!deedHistoryAvailable(parcel)) return "";
  return `<div class="tt-veh deedhist" data-sub="${esc(parcel.sub)}" data-lot="${esc(parcel.lot)}" ` +
    `data-blc="${esc(parcel.blc || "")}"><b>Recorded deeds &amp; mortgages</b>` +
    `<div class="dh-load"><span class="dh-spin"></span>Reading county records… (up to ~15 s the first time)</div></div>`;
}

function deedFullSearchLink() {
  return `<a href="${PULASKI_DEEDS_ACCEPT_URL}" target="_blank" rel="noopener">PulaskiDeeds ↗</a>`;
}

const DH_MONTH = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function dhDate(d) {
  const s = String(d || "");
  if (s.length < 6) return s;
  return `${DH_MONTH[+s.slice(4, 6)] || s.slice(4, 6)} ${s.slice(0, 4)}`;
}
function dhParties(list) {
  const a = (list || []).filter(Boolean);
  if (!a.length) return "";
  return esc(a.slice(0, 3).join("; ") + (a.length > 3 ? " …" : ""));
}

export function renderDeedHistory(d) {
  if (!d || d.error || !d.docs) {
    return `<b>Recorded deeds &amp; mortgages</b><div>Couldn't reach the county records service. ` +
      `Look this parcel up on ${deedFullSearchLink()}.</div>`;
  }
  if (!d.docs.length) {
    return `<b>Recorded deeds &amp; mortgages</b><div>No documents recorded against this parcel's ` +
      `legal description since ${d.since}. Older records may exist on ${deedFullSearchLink()}.</div>`;
  }
  const row = (x) => {
    const parties = (x.grantor && x.grantor.length) || (x.grantee && x.grantee.length)
      ? `<div class="dh-parties">${dhParties(x.grantor)} <span class="dh-arrow">→</span> ${dhParties(x.grantee)}</div>` : "";
    return `<div class="dh-row${x.chain ? " dh-chain" : ""}">` +
      `<div class="dh-top"><span class="dh-date">${dhDate(x.date)}</span>` +
      `<span class="dh-type">${esc((x.type || "").toLowerCase())}</span>` +
      `<span class="dh-inst">${deedDocLink(x.inst, x.inst) || esc(x.inst)}</span></div>${parties}</div>`;
  };
  const chain = d.docs.filter((x) => x.chain);
  const rest = d.docs.filter((x) => !x.chain);
  let html = `<b>Recorded deeds &amp; mortgages</b>`;
  if (d.owner && d.owner.length) {
    html += `<div class="dh-owner">Current owner (per deeds): <b>${dhParties(d.owner)}</b></div>`;
  }
  if (chain.length) html += `<div class="dh-group">${chain.map(row).join("")}</div>`;
  if (rest.length) {
    html += `<div class="dh-sub">Other records on this parcel</div>` +
      `<div class="dh-group dh-dim">${rest.map(row).join("")}</div>`;
  }
  html += `<div class="dh-foot">Since ${d.since} · full record &amp; images on ${deedFullSearchLink()} · unofficial</div>`;
  return html;
}

export function loadDeedHistory(popup, parcel) {
  const el = popup.getElement && popup.getElement();
  const box = el && el.querySelector(".deedhist");
  if (!box) return;
  fetchDeedHistory(parcel).then((d) => {
    // the popup may have been closed/replaced while we waited
    if (!box.isConnected) return;
    box.innerHTML = renderDeedHistory(d);
  });
}
