/* ===========================================================================
   Watch — every surveillance device this project can put on a map, what each
   one does, and the records that paid for it.

   Data comes from pipeline/build_surveillance.py:
     devices.geojson   ARDOT's published cameras + LRPD's FOIA'd plate readers
                       + OpenStreetMap surveillance tagging + field sightings
     programs.json     what each family of device is and who runs it
     documents.json    the paper trail, with facts extracted from each record
   =========================================================================== */
(function () {
  "use strict";

  const BASE = "data/surveillance/";
  const $ = (id) => document.getElementById(id);

  let D = null;              // {devices, programs, documents, meta}
  let wmap = null;           // this view's own MapLibre instance
  let drawn = false;
  let mapReady = false;
  const off = new Set();     // families toggled off in the legend
  let queue = [];            // documents the reader has submitted, kept locally

  /* ------------------------------------------------------------- families */
  const FAM = {
    alpr: { label: "Plate readers", color: "#f0596a",
            blurb: "Reads and stores every passing plate" },
    gunshot: { label: "Gunshot sensors", color: "#c77dff",
               blurb: "Listens for gunfire and locates it" },
    traffic: { label: "Traffic cameras", color: "#4aa8ff",
               blurb: "Live video, published by the state" },
    camera: { label: "Other cameras", color: "#7f8fa6",
              blurb: "Mapped cameras of other kinds" },
    enforcement: { label: "Photo enforcement", color: "#ff9f43",
                   blurb: "Speed or red-light enforcement" },
    sighting: { label: "Field sightings", color: "#f3d54c",
                blurb: "Photographed on a pole, identified by hand" },
  };
  const famOf = (f) => FAM[f.properties.fam] || FAM.camera;

  const SRC_LABEL = {
    ardot: "Published by ARDOT on IDriveArkansas",
    foia: "From LRPD's own list, released under FOIA",
    osm: "Mapped by volunteers in OpenStreetMap",
    sighting: "Spotted in the field and checked by hand",
  };
  const CONF_TEXT = {
    confirmed: "Confirmed", likely: "Likely", probable: "Probable",
    uncertain: "Not identified",
  };

  const esc = (s) => String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
  const money = (n) => "$" + Math.round(n).toLocaleString("en-US");

  function niceDate(iso) {
    if (!iso) return "";
    const [y, m, d] = iso.split("-").map(Number);
    if (!y) return iso;
    const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                 "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    return d ? `${d} ${MON[m - 1]} ${y}` : `${MON[m - 1]} ${y}`;
  }
  // Sighting timelines are year-month only ("2024-08").
  function niceMonth(s) {
    const [y, m] = String(s).split("-").map(Number);
    const MON = ["January", "February", "March", "April", "May", "June", "July",
                 "August", "September", "October", "November", "December"];
    return m ? `${MON[m - 1]} ${y}` : String(s);
  }

  /* ------------------------------------------------------------------ load */
  async function load() {
    const names = ["devices.geojson", "programs.json", "documents.json", "meta.json"];
    try {
      const [devices, programs, documents, meta] = await Promise.all(
        names.map((n) => fetch(BASE + n).then((r) => {
          if (!r.ok) throw new Error(n + " " + r.status);
          return r.json();
        })));
      D = { devices, programs, documents, meta };
    } catch (e) {
      $("wMeta").innerHTML =
        "Could not load the device data. Run <code>python pipeline/build_surveillance.py</code> " +
        "to generate <code>web/data/surveillance/</code>.";
      return;
    }
    drawn = true;
    window.__watch = { data: () => D, render, initMap, focus, showDetail,
                       map: () => wmap, applyFilter, queue: () => queue };
    render();
  }

  function render() {
    renderMeta();
    renderKey();
    renderGuide();
    renderSightings();
    renderMoney();
    renderGaps();
    renderForm();
    renderSources();
    initMap();
  }

  /* ------------------------------------------------------------------ meta */
  function renderMeta() {
    const m = D.meta, c = m.counts || {};
    const unlisted = D.devices.features.filter((f) => !f.properties.public).length;
    $("wMeta").innerHTML =
      `<b>${m.devices}</b> devices mapped across Pulaski County — ` +
      `<b>${c.alpr || 0}</b> plate readers, <b>${c.traffic || 0}</b> state traffic cameras, ` +
      `<b>${c.gunshot || 0}</b> gunshot sensors, <b>${c.sighting || 0}</b> field sightings. ` +
      `<b>${unlisted}</b> were never published by the agency that runs them — they are ` +
      `here because a volunteer mapped them or someone photographed the pole. ` +
      `<b>${m.documents}</b> records in the paper trail. Built ${niceDate(m.generated)}.`;
  }

  function renderKey() {
    const counts = {};
    for (const f of D.devices.features) {
      const k = f.properties.fam;
      counts[k] = (counts[k] || 0) + 1;
    }
    const box = $("wKey");
    box.innerHTML = "";
    for (const key of Object.keys(FAM)) {
      if (!counts[key]) continue;
      const fam = FAM[key];
      const item = document.createElement("button");
      item.className = "wKeyItem" + (off.has(key) ? " off" : "");
      item.title = fam.blurb;
      item.innerHTML =
        `<span class="wKeyDot" style="background:${fam.color}"></span>` +
        `<span>${esc(fam.label)}</span><span class="wKeyN">${counts[key]}</span>`;
      item.addEventListener("click", () => {
        if (off.has(key)) off.delete(key); else off.add(key);
        renderKey();
        applyFilter();
      });
      box.appendChild(item);
    }
  }

  /* ------------------------------------------------------------------- map */
  function basemapStyle() {
    return {
      version: 8,
      sources: {
        carto: {
          type: "raster", tileSize: 256,
          tiles: ["a", "b", "c", "d"].map(
            (s) => `https://${s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png`),
          attribution: "© OpenStreetMap contributors © CARTO",
        },
      },
      layers: [
        { id: "bg", type: "background", paint: { "background-color": "#05070c" } },
        { id: "carto", type: "raster", source: "carto",
          paint: { "raster-opacity": 0.5, "raster-saturation": -0.35 } },
      ],
    };
  }

  function initMap() {
    if (wmap || typeof maplibregl === "undefined") return;
    const holder = $("wMap");
    if (!holder || !holder.offsetWidth) return;   // still hidden: wait for the tab

    wmap = new maplibregl.Map({
      container: "wMap",
      style: basemapStyle(),
      center: [-92.33, 34.76],
      zoom: 10.2,
      attributionControl: false,
    });
    wmap.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-left");
    wmap.addControl(new maplibregl.AttributionControl({ compact: true }), "bottom-right");
    wmap.on("load", () => {
      mapReady = true;
      addLayers();
      applyFilter();
    });
  }

  function addLayers() {
    wmap.addSource("dev", { type: "geojson", data: D.devices });

    // Plate readers get a cone showing the direction they face, drawn from the
    // OSM "direction" tag. Only readers carry one.
    wmap.addSource("cones", { type: "geojson", data: coneData() });
    wmap.addLayer({
      id: "cone", type: "fill", source: "cones",
      paint: { "fill-color": FAM.alpr.color, "fill-opacity": 0.13 },
    });

    const colour = ["match", ["get", "fam"]];
    for (const k of Object.keys(FAM)) colour.push(k, FAM[k].color);
    colour.push("#7f8fa6");

    wmap.addLayer({
      id: "dev-dot", type: "circle", source: "dev",
      filter: ["!=", ["get", "fam"], "sighting"],
      paint: {
        "circle-color": colour,
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 2.6, 13, 5, 16, 8],
        "circle-stroke-width": 1,
        "circle-stroke-color": "rgba(0,0,0,0.55)",
        "circle-opacity": 0.92,
      },
    });
    // Sightings sit on top and read as targets rather than dots.
    wmap.addLayer({
      id: "dev-sight", type: "circle", source: "dev",
      filter: ["==", ["get", "fam"], "sighting"],
      paint: {
        "circle-color": "rgba(0,0,0,0)",
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 5, 13, 9, 16, 14],
        "circle-stroke-width": 2.4,
        "circle-stroke-color": FAM.sighting.color,
      },
    });

    for (const id of ["dev-dot", "dev-sight"]) {
      wmap.on("click", id, (e) => showDetail(e.features[0]));
      wmap.on("mouseenter", id, () => { wmap.getCanvas().style.cursor = "pointer"; });
      wmap.on("mouseleave", id, () => { wmap.getCanvas().style.cursor = ""; });
    }
    $("wMapNote").textContent =
      "Zoom in: the readers cluster on the arterials and at the city edge.";
  }

  // A 60° wedge, 90 m long, pointing the way the camera looks.
  function coneData() {
    const feats = [];
    for (const f of D.devices.features) {
      const p = f.properties;
      if (p.dir == null || p.fam !== "alpr") continue;
      const [lon, lat] = f.geometry.coordinates;
      const mPerDegLat = 111320, mPerDegLon = 111320 * Math.cos(lat * Math.PI / 180);
      const ring = [[lon, lat]];
      for (let a = -30; a <= 30; a += 6) {
        const rad = (p.dir + a) * Math.PI / 180;
        ring.push([lon + (Math.sin(rad) * 90) / mPerDegLon,
                   lat + (Math.cos(rad) * 90) / mPerDegLat]);
      }
      ring.push([lon, lat]);
      feats.push({ type: "Feature", properties: { fam: "alpr" },
                   geometry: { type: "Polygon", coordinates: [ring] } });
    }
    return { type: "FeatureCollection", features: feats };
  }

  function applyFilter() {
    if (!mapReady) return;
    const onlyUnlisted = $("wOnlyUnlisted").checked;
    const showCones = $("wCones").checked;
    const fams = Object.keys(FAM).filter((k) => !off.has(k));
    const base = ["in", ["get", "fam"], ["literal", fams]];
    const rule = onlyUnlisted ? ["all", base, ["!=", ["get", "public"], 1]] : base;
    wmap.setFilter("dev-dot", ["all", rule, ["!=", ["get", "fam"], "sighting"]]);
    wmap.setFilter("dev-sight", ["all", rule, ["==", ["get", "fam"], "sighting"]]);
    wmap.setLayoutProperty("cone", "visibility",
      showCones && !off.has("alpr") ? "visible" : "none");
  }

  function focus(lon, lat, zoom) {
    if (!wmap) return;
    try { wmap.flyTo({ center: [lon, lat], zoom: zoom || 16, speed: 1.3 }); }
    catch (e) { wmap.jumpTo({ center: [lon, lat], zoom: zoom || 16 }); }
  }

  /* ---------------------------------------------------------- detail card */
  function showDetail(feature) {
    const p = feature.properties;
    const prog = D.programs[p.prog] || {};
    const fam = FAM[p.fam] || FAM.camera;
    const box = $("wDetail");
    const rows = [];

    const row = (k, v) => v ? `<div class="wRow"><dt>${k}</dt><dd>${v}</dd></div>` : "";
    rows.push(row("Programme", esc(prog.name || p.prog)));
    rows.push(row("Operated by", esc(p.op || prog.operator)));
    if (p.make) rows.push(row("Make", esc(p.make)));
    if (p.model) rows.push(row("Model", esc(p.model)));
    if (p.status) rows.push(row("Status", esc(p.status)));
    if (p.route) rows.push(row("Route", "I-" + esc(p.route)));
    if (p.addr) rows.push(row("Address", esc(p.addr)));
    if (p.dir != null) rows.push(row("Faces", Math.round(p.dir) + "°"));
    rows.push(row("Record", esc(SRC_LABEL[p.src] || p.src)));
    if (p.osm_seen) rows.push(row("Also", "Independently mapped by a volunteer"));

    let extra = "";
    if (p.fam === "sighting") {
      const timeline = (typeof p.timeline === "string" ? JSON.parse(p.timeline) : p.timeline) || [];
      extra += `<span class="wConf ${esc(p.conf)}">${esc(CONF_TEXT[p.conf] || p.conf)}</span>`;
      if (timeline.length) {
        extra += "<ul class=\"wTimeline\">" + timeline.map(
          (t) => `<li><b>${esc(niceMonth(t.date))}</b> — ${esc(t.saw)}</li>`).join("") + "</ul>";
      }
      if (p.why) extra += `<p>${esc(p.why)}</p>`;
      if (p.confirm) extra += `<p class="wAsk"><b>To settle it:</b> ${esc(p.confirm)}</p>`;
      if (p.near_cam_m != null) {
        extra += `<p class="wAsk">Nearest published state camera: ` +
          `${p.near_cam_m} m away (${esc(p.near_cam)}).</p>`;
      }
    } else if (prog.what_it_captures) {
      extra += `<p>${esc(prog.what_it_captures)}</p>`;
    }
    if (p.url) {
      const label = p.src === "ardot" ? "Open the live stream"
        : p.src === "sighting" ? "Open in Street View"
        : p.src === "foia" ? "The FOIA response it came from"
        : "See the map record";
      extra += `<p><a href="${esc(p.url)}" target="_blank" rel="noopener">${label} →</a></p>`;
    }

    box.innerHTML =
      `<button class="wClose" title="Close">×</button>` +
      `<span class="wTag" style="background:${fam.color}22;color:${fam.color}">${esc(fam.label)}</span>` +
      `<h3>${esc(p.lbl)}</h3>` +
      (p.where ? `<div class="wProgWho">${esc(p.where)}</div>` : "") +
      `<dl>${rows.join("")}</dl>${extra}`;
    box.hidden = false;
    box.querySelector(".wClose").addEventListener("click", () => { box.hidden = true; });
  }

  /* ----------------------------------------------------------- field guide */
  function renderGuide() {
    const counts = {};
    for (const f of D.devices.features) {
      counts[f.properties.prog] = (counts[f.properties.prog] || 0) + 1;
    }
    const order = Object.keys(D.programs).sort(
      (a, b) => (counts[b] || 0) - (counts[a] || 0));
    const box = $("wGuide");
    box.innerHTML = "";
    for (const id of order) {
      const p = D.programs[id];
      if (id === "unidentified") continue;          // covered by the sightings
      const fam = FAM[p.family] || FAM.camera;
      const n = counts[id] || 0;
      const docs = (p.documents || []).map((docId) => {
        const doc = D.documents.find((d) => d.id === docId);
        return doc ? `<a href="${esc(doc.url)}" target="_blank" rel="noopener">` +
          `${esc(niceDate(doc.date))} · ${esc(doc.kind.replace(/_/g, " "))}</a>` : "";
      }).join("");
      const dd = (label, value) => value
        ? `<dt>${label}</dt><dd>${esc(value)}</dd>` : "";
      const card = document.createElement("div");
      card.className = "wProg";
      card.style.borderLeftColor = fam.color;
      card.innerHTML =
        (n ? `<span class="wProgN">${n} mapped</span>` : "") +
        `<h3>${esc(p.name)}</h3>` +
        `<div class="wProgWho">${esc(p.operator || "")}${p.vendor ? " · " + esc(p.vendor) : ""}</div>` +
        "<dl>" +
        dd("What it is", p.what_it_is) +
        dd("What it captures", p.what_it_captures) +
        dd("How to spot it", p.how_to_spot) +
        dd("How long it keeps it", p.retention) +
        dd("Who can see it", p.who_can_see_it) +
        dd("How many", p.count_note) +
        dd("The money", p.money) +
        dd("How it was bought", p.procurement_note) +
        "</dl>" +
        (docs ? `<div class="wDocLinks">${docs}</div>` : "") +
        ((p.sources || []).length
          ? `<div class="wDocLinks">` + p.sources.map(
              (s) => `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.label)}</a>`
            ).join("") + "</div>"
          : "");
      box.appendChild(card);
    }
  }

  /* ------------------------------------------------------------- sightings */
  function renderSightings() {
    const sights = D.devices.features.filter((f) => f.properties.fam === "sighting");
    const unlisted = sights.filter((f) => (f.properties.near_cam_m || 0) > 100).length;
    $("wSightNote").innerHTML =
      `${sights.length} sites photographed from the road and checked against the ` +
      `state's published camera list. ${unlisted} of them sit more than 100 m from ` +
      `any published camera, which means the equipment on them is not what the ` +
      `public map is describing. Each card says how confident the identification is.`;

    const box = $("wSightings");
    box.innerHTML = "";
    for (const f of sights) {
      const p = f.properties;
      const [lon, lat] = f.geometry.coordinates;
      const prog = D.programs[p.prog] || {};
      const timeline = (p.timeline || []).map(
        (t) => `<li><b>${esc(niceMonth(t.date))}</b> — ${esc(t.saw)}</li>`).join("");
      const card = document.createElement("div");
      card.className = "wSight";
      card.innerHTML =
        `<span class="wConf ${esc(p.conf)}">${esc(CONF_TEXT[p.conf] || p.conf)}</span>` +
        (prog.short ? ` <span class="wPill">${esc(prog.short)}</span>` : "") +
        `<h3>${esc(p.lbl)}</h3>` +
        `<div class="wSightWhere">${esc(p.where)}</div>` +
        (timeline ? `<ul class="wTimeline">${timeline}</ul>` : "") +
        (p.why ? `<p>${esc(p.why)}</p>` : "") +
        (p.confirm ? `<p class="wAsk"><b>To settle it:</b> ${esc(p.confirm)}</p>` : "") +
        (p.note ? `<p class="wAsk">${esc(p.note)}</p>` : "") +
        `<div class="wSightFoot">` +
          `<a href="${esc(p.url)}" target="_blank" rel="noopener">Street View →</a>` +
          `<button class="wBtn ghost wGo" type="button">Show on the map</button>` +
          (p.near_cam_m != null
            ? `<span class="wPill">${p.near_cam_m} m from ${esc(p.near_cam)}</span>` : "") +
        `</div>`;
      card.querySelector(".wGo").addEventListener("click", () => {
        focus(lon, lat, 17);
        $("wMap").scrollIntoView({ behavior: "smooth", block: "center" });
        showDetail(f);
      });
      box.appendChild(card);
    }
  }

  /* ----------------------------------------------------------- money trail */
  function renderMoney() {
    const box = $("wMoney");
    box.innerHTML = "";
    const docs = D.documents.slice().sort((a, b) => (b.date || "").localeCompare(a.date || ""));
    for (const d of docs) {
      const top = (d.amounts || [])[0];
      const f = d.facts || {};
      const chips = [];
      if (f.resolutions && f.resolutions.length) {
        chips.push(`<span class="wFact">Resolution <b>${esc(f.resolutions.join(", "))}</b></span>`);
      }
      if (f.accounts && f.accounts.length) {
        chips.push(`<span class="wFact">Account <b>${esc(f.accounts.join(", "))}</b></span>`);
      }
      if (f.cooperative_contracts && f.cooperative_contracts.length) {
        chips.push(`<span class="wFact">Bought via <b>${esc(f.cooperative_contracts[0])}</b></span>`);
      }
      if (f.vendors && f.vendors.length) {
        chips.push(`<span class="wFact">Vendor <b>${esc(f.vendors.slice(0, 2).join(", "))}</b></span>`);
      }
      const progs = (d.programs || []).map(
        (id) => (D.programs[id] || {}).short || id).join(" · ");
      const quote = (d.parts || {}).fiscal_impact || (d.parts || {}).synopsis || "";
      const el = document.createElement("div");
      el.className = "wDoc";
      el.innerHTML =
        `<div><div class="wDocDate">${esc(niceDate(d.date))}</div>` +
        (top ? `<div class="wDocAmt">${esc(money(top.value))}</div>` : "") + `</div>` +
        `<div><h3>${esc(d.title)}</h3>` +
        `<div class="wDocDate">${esc(d.body || "")}${progs ? " · " + esc(progs) : ""}</div>` +
        (chips.length ? `<div class="wDocFacts">${chips.join("")}</div>` : "") +
        (quote ? `<p class="wQuote">${esc(quote.slice(0, 340))}</p>` : "") +
        (d.note ? `<p class="wQuote">${esc(d.note)}</p>` : "") +
        `<p style="margin:9px 0 0"><a class="wSrc" href="${esc(d.url)}" target="_blank" ` +
        `rel="noopener">Read the original (${esc(d.source)}) →</a></p></div>`;
      box.appendChild(el);
    }
  }

  /* ------------------------------------------------------------------ gaps */
  const GAPS = [
    ["Real Time Crime Center cameras",
     "75 cameras were approved in February 2023 for about $215,800, and the " +
     "subscriptions that keep them running still appear in the city's budget. " +
     "The department has never published where they are, so none of them can " +
     "be a pin on this map."],
    ["Most gunshot sensors",
     "A gunshot detection array needs roughly 20 to 25 microphones per square " +
     "mile to triangulate. Little Rock's coverage area started at two square " +
     "miles. The handful shown here are the ones volunteers happened to spot; " +
     "the real number is far higher and the layout is not public."],
    ["Plate readers on patrol cars",
     "Mobile readers scan while the car drives, so they have no fixed location " +
     "to map at all. They feed the same searchable database as the pole-mounted " +
     "ones."],
    ["Wrong-way detection cameras",
     "ARDOT's own description of a wrong-way site includes closed-circuit " +
     "cameras and illuminators. Those cameras are not published as public " +
     "traffic feeds, so this map can only show the ramps where the equipment " +
     "has been seen."],
    ["Private cameras that police can reach",
     "Businesses and residents can register or integrate their own cameras with " +
     "a real time crime centre. A registry like that turns private cameras into " +
     "part of the public system without any of them appearing on a public map."],
    ["Everything indoors",
     "Shops, schools, buses, lobbies and car parks. This map is limited to " +
     "devices visible from public space or listed in a public record."],
  ];

  function renderGaps() {
    const box = $("wGaps");
    box.innerHTML = "";
    for (const [title, text] of GAPS) {
      const el = document.createElement("div");
      el.className = "wGap";
      el.innerHTML = `<h3>${esc(title)}</h3><p>${esc(text)}</p>`;
      box.appendChild(el);
    }
  }

  /* ------------------------------------------------------- add to the trail */
  const QKEY = "watch.submissions.v1";

  function loadQueue() {
    try { queue = JSON.parse(localStorage.getItem(QKEY) || "[]"); }
    catch (e) { queue = []; }
  }
  function saveQueue() {
    try { localStorage.setItem(QKEY, JSON.stringify(queue)); } catch (e) { /* private mode */ }
  }

  function commandFor(item) {
    const parts = ["python pipeline/surveillance_docs.py add", `"${item.url}"`];
    for (const p of item.programs || []) parts.push(`--program ${p}`);
    if (item.title) parts.push(`--title "${item.title.replace(/"/g, "'")}"`);
    if (item.date) parts.push(`--date ${item.date}`);
    if (item.note) parts.push(`--note "${item.note.replace(/"/g, "'")}"`);
    return parts.join(" ");
  }

  function say(msg, isError) {
    const el = $("wDocMsg");
    el.textContent = msg;
    el.className = "wFormMsg" + (isError ? " err" : "");
  }

  async function copy(text, okMsg) {
    try {
      await navigator.clipboard.writeText(text);
      say(okMsg);
    } catch (e) {
      say("Could not reach the clipboard — select the command below and copy it.", true);
    }
  }

  function renderForm() {
    const select = $("wDocProgram");
    select.innerHTML = `<option value="">Which programme? (optional)</option>`;
    for (const id of Object.keys(D.programs)) {
      const o = document.createElement("option");
      o.value = id;
      o.textContent = D.programs[id].name;
      select.appendChild(o);
    }
    loadQueue();
    renderQueue();

    $("wDocAdd").onclick = () => {
      const url = $("wDocUrl").value.trim();
      if (!/^https?:\/\//i.test(url)) { say("Give it a full http(s) link.", true); return; }
      if (queue.some((q) => q.url === url)) { say("That link is already in the list.", true); return; }
      const program = $("wDocProgram").value;
      queue.push({
        url,
        title: $("wDocTitle").value.trim(),
        date: $("wDocDate").value,
        note: $("wDocNote").value.trim(),
        programs: program ? [program] : [],
        added: new Date().toISOString().slice(0, 10),
        added_by: "reader",
      });
      saveQueue();
      renderQueue();
      $("wDocUrl").value = ""; $("wDocTitle").value = ""; $("wDocNote").value = "";
      say("Saved in this browser. Copy the command to file it into the project.");
    };

    $("wDocCopy").onclick = () => {
      if (!queue.length) { say("Nothing to copy yet.", true); return; }
      copy(queue.map(commandFor).join("\n"),
           `Copied ${queue.length} command${queue.length > 1 ? "s" : ""}.`);
    };
    $("wDocExport").onclick = () => {
      if (!queue.length) { say("Nothing to copy yet.", true); return; }
      copy(JSON.stringify({ submissions: queue }, null, 1),
           "Copied as JSON — save it and run: python pipeline/surveillance_docs.py queue <file>");
    };
  }

  function renderQueue() {
    const box = $("wQueue");
    box.innerHTML = "";
    if (!queue.length) return;
    queue.forEach((item, i) => {
      const el = document.createElement("div");
      el.className = "wQItem";
      el.innerHTML =
        `<div><div>${esc(item.title || item.url)}</div>` +
        (item.title ? `<a href="${esc(item.url)}" target="_blank" rel="noopener">${esc(item.url)}</a>` : "") +
        `<div class="wCmd">${esc(commandFor(item))}</div></div>` +
        `<button class="wQrm" title="Remove">×</button>`;
      el.querySelector(".wQrm").addEventListener("click", () => {
        queue.splice(i, 1); saveQueue(); renderQueue();
      });
      box.appendChild(el);
    });
  }

  function renderSources() {
    const srcs = (D.meta.sources || []).map(
      (s) => s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.name)}</a>`
                   : esc(s.name)).join(" · ");
    $("wSources").innerHTML =
      "Sources: " + srcs + ". Plate reader and gunshot sensor positions come from " +
      "OpenStreetMap, © OpenStreetMap contributors, ODbL. Little Rock's own reader " +
      "list was released under FOIA request PDFOIA-2025-4004 and published by the " +
      "Arkansas Times. Device identifications made from photographs are labelled " +
      "with a confidence and may be wrong — corrections are welcome.";
  }

  /* ------------------------------------------------------------------ wire */
  document.addEventListener("viewchange", (e) => {
    if (e.detail.view !== "watch") return;
    if (!drawn) load();
    // The container had no size until now, so the map is built on first view.
    requestAnimationFrame(() => {
      if (!wmap) initMap();
      else wmap.resize();
    });
  });

  document.addEventListener("DOMContentLoaded", () => {
    const link = $("watchLink");
    if (link) {
      link.addEventListener("click", (e) => {
        e.preventDefault();
        const btn = document.querySelector('.tabBtn[data-view="watch"]');
        if (btn) btn.click();
      });
    }
    for (const id of ["wOnlyUnlisted", "wCones"]) {
      const el = $(id);
      if (el) el.addEventListener("change", applyFilter);
    }
  });
})();
