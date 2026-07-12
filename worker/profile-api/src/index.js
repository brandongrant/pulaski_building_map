/* Pulaski property-profile API (roadmap §6.5) — read-only Cloudflare Worker
 * over the Phase 1 Neon/PostGIS canonical model loaded by pipeline/load_db.py.
 *
 *   GET /v1/properties/{property_id | source parcel id}
 *   GET /v1/properties/{...}/timeline
 *   GET /v1/properties/{...}/sources
 *   GET /v1/properties/{...}/neighbors
 *   GET /health
 *
 * The path key accepts either the canonical UUID or an official parcel id
 * ("43L-109.00-373.00" — anything non-UUID is normalized and looked up by
 * parcel_id_normalized), because the map's tiles carry the official id.
 * Responses cache 6 h in the Cache API; the database reloads at most daily.
 */
import { neon } from "@neondatabase/serverless";

const CACHE_SECONDS = 6 * 3600;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}

function cors(resp) {
  resp.headers.set("Access-Control-Allow-Origin", "*");
  resp.headers.set("Access-Control-Allow-Methods", "GET, OPTIONS");
  resp.headers.set("Access-Control-Max-Age", "86400");
  return resp;
}

const normParcel = (s) => String(s || "").replace(/[^0-9A-Za-z]/g, "").toUpperCase();

async function resolveProperty(sql, key) {
  if (UUID_RE.test(key)) {
    const r = await sql`
      select property_id from property where property_id = ${key}`;
    return r[0]?.property_id || null;
  }
  const r = await sql`
    select property_id from property
    where jurisdiction_id = 'us-ar-pulaski'
      and parcel_id_normalized = ${normParcel(key)}`;
  return r[0]?.property_id || null;
}

const coreQuery = (sql, id) => sql`
  select p.property_id, p.source_parcel_id, p.situs_address, p.city, p.state,
         st_x(p.centroid) as lon, st_y(p.centroid) as lat,
         s.owner_name_raw, s.year_built, s.stories, s.assessor_sqft,
         s.land_value, s.improvement_value, s.total_value, s.assessed_value,
         s.property_type, s.legal_description, s.subdivision, s.lot, s.block,
         s.as_of_date, s.attributes as snapshot_attributes
  from property p
  left join lateral (
    select * from property_snapshot ps
    where ps.property_id = p.property_id
    order by ps.as_of_date desc limit 1) s on true
  where p.property_id = ${id}`;

const buildingsQuery = (sql, id) => sql`
  select building_id, source_building_id, year_built, building_category,
         stories, assessor_sqft, improvement_value, footprint_sqft,
         is_primary_building, match_method, match_confidence,
         st_x(centroid) as lon, st_y(centroid) as lat
  from building where property_id = ${id}
  order by is_primary_building desc nulls last, footprint_sqft desc nulls last
  limit 50`;

const timelineQuery = (sql, id) => sql`
  select e.event_id, e.event_type, e.event_subtype, e.event_at::date as event_at,
         e.observed_at::date as observed_at, e.title, e.summary, e.status,
         e.amount, e.source_event_key, e.attributes,
         m.match_method, m.match_confidence,
         ds.slug as source_slug, ds.name as source_name,
         coalesce(sr.source_record_url, ds.source_url) as source_url
  from event_property_match m
  join event e on e.event_id = m.event_id
  join source_record sr on sr.source_record_id = e.source_record_id
  join data_source ds on ds.source_id = sr.source_id
  where m.property_id = ${id}
  order by e.event_at desc
  limit 250`;

const interestsQuery = (sql, id) => sql`
  select en.entity_id, en.entity_type, en.display_name, i.role, i.confidence
  from property_interest i
  join entity en on en.entity_id = i.entity_id
  where i.property_id = ${id} and i.valid_to is null
  order by i.role, en.display_name
  limit 20`;

const sourcesQuery = (sql) => sql`
  select ds.slug, ds.name, ds.source_url, ds.entity_grain, ds.refresh_cadence,
         max(ir.completed_at)::date as last_ingest,
         max(ir.source_effective_at)::date as source_effective
  from data_source ds
  left join ingest_run ir on ir.source_id = ds.source_id and ir.status = 'completed'
  group by 1, 2, 3, 4, 5
  order by 1`;

const neighborsQuery = (sql, id) => sql`
  select p.property_id, p.source_parcel_id, p.situs_address, p.city,
         st_x(p.centroid) as lon, st_y(p.centroid) as lat,
         s.owner_name_raw, s.total_value, s.year_built
  from property p
  left join lateral (
    select owner_name_raw, total_value, year_built from property_snapshot ps
    where ps.property_id = p.property_id
    order by ps.as_of_date desc limit 1) s on true
  where p.property_id <> ${id}
    and st_dwithin(p.centroid,
                   (select centroid from property where property_id = ${id}),
                   0.0012)          -- ~120 m at this latitude, uses the gist index
  order by st_distance(p.centroid,
                       (select centroid from property where property_id = ${id}))
  limit 12`;

function yearsBetween(dateStr, now) {
  if (!dateStr) return null;
  const t = new Date(dateStr).getTime();
  if (!isFinite(t)) return null;
  return Math.round(((now - t) / (365.25 * 24 * 3600e3)) * 10) / 10;
}

function timelineRow(e) {
  return {
    event_id: e.event_id,
    event_type: e.event_type,
    event_subtype: e.event_subtype,
    event_at: e.event_at,
    observed_at: e.observed_at,
    title: e.title,
    summary: e.summary,
    status: e.status,
    amount: e.amount === null ? null : Number(e.amount),
    attributes: e.attributes,
    source: {
      slug: e.source_slug,
      name: e.source_name,
      record_id: e.source_event_key,
      url: e.source_url,
    },
    match: { method: e.match_method, confidence: Number(e.match_confidence) },
  };
}

function buildProfile(core, buildings, timeline, interests, sources) {
  const now = Date.now();
  const num = (v) => (v === null || v === undefined ? null : Number(v));
  const imp = num(core.improvement_value);
  const land = num(core.land_value);
  const sqft = num(core.assessor_sqft);
  const lastTransfer = timeline.find((e) => e.event_type === "ownership_transfer");
  const lastMajorPermit = timeline.find(
    (e) => (e.event_type === "permit_issued" || e.event_type === "demolition_permit")
      && Number(e.amount) >= 10000);
  const warnings = [];
  if (!core.as_of_date) warnings.push("No assessor snapshot for this parcel.");
  const lowConf = timeline.filter((e) => Number(e.match_confidence) < 0.8).length;
  if (lowConf) warnings.push(`${lowConf} timeline item(s) matched with low confidence.`);
  const freshness = {};
  for (const s of sources) {
    freshness[s.slug] = s.source_effective || s.last_ingest || null;
  }
  return {
    property_id: core.property_id,
    parcel_id: core.source_parcel_id,
    address: { label: core.situs_address, city: core.city, state: core.state },
    location: core.lon === null ? null : { lon: Number(core.lon), lat: Number(core.lat) },
    current: {
      owner_display: core.owner_name_raw,
      owners: interests.map((i) => ({
        entity_id: i.entity_id, name: i.display_name,
        entity_type: i.entity_type, role: i.role,
      })),
      year_built: core.year_built,
      stories: num(core.stories),
      property_type: core.property_type,
      assessor_sqft: sqft,
      land_value: land,
      improvement_value: imp,
      total_value: num(core.total_value),
      assessed_value: num(core.assessed_value),
      legal: {
        description: core.legal_description,
        subdivision: core.subdivision, lot: core.lot, block: core.block,
      },
      as_of: core.as_of_date,
    },
    metrics: {
      improvement_value_per_sqft: imp && sqft ? Math.round((imp / sqft) * 100) / 100 : null,
      improvement_to_land_ratio: imp && land ? Math.round((imp / land) * 100) / 100 : null,
      years_since_major_permit: yearsBetween(lastMajorPermit?.event_at, now),
      ownership_tenure_years: yearsBetween(lastTransfer?.event_at, now),
    },
    buildings: buildings.map((b) => ({
      building_id: b.building_id,
      source_building_id: b.source_building_id,
      is_primary: b.is_primary_building,
      year_built: b.year_built,
      category: b.building_category,
      stories: num(b.stories),
      assessor_sqft: num(b.assessor_sqft),
      footprint_sqft: num(b.footprint_sqft),
      improvement_value: num(b.improvement_value),
      match: { method: b.match_method, confidence: num(b.match_confidence) },
    })),
    timeline: timeline.map(timelineRow),
    sources: sources.map((s) => ({
      slug: s.slug, name: s.name, url: s.source_url,
      last_ingest: s.last_ingest, source_effective: s.source_effective,
    })),
    freshness,
    warnings,
  };
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (request.method === "OPTIONS") return cors(new Response(null, { status: 204 }));
    if (url.pathname === "/health") return cors(json({ ok: true }));

    const m = url.pathname.match(
      /^\/v1\/properties\/([^/]+)(?:\/(timeline|sources|neighbors))?$/);
    if (!m) return cors(json({ error: "not found" }, 404));
    const [, rawKey, section] = m;

    const cache = caches.default;
    const cacheKey = new Request(url.toString(), { method: "GET" });
    const hit = await cache.match(cacheKey);
    if (hit) return cors(new Response(hit.body, hit));

    if (!env.DATABASE_URL) return cors(json({ error: "DATABASE_URL not configured" }, 503));
    const sql = neon(env.DATABASE_URL);

    try {
      const id = await resolveProperty(sql, decodeURIComponent(rawKey));
      if (!id) return cors(json({ error: "property not found" }, 404));

      let body;
      if (section === "timeline") {
        body = { property_id: id, timeline: (await timelineQuery(sql, id)).map(timelineRow) };
      } else if (section === "sources") {
        body = { property_id: id, sources: await sourcesQuery(sql) };
      } else if (section === "neighbors") {
        body = { property_id: id, neighbors: await neighborsQuery(sql, id) };
      } else {
        const [core, buildings, timeline, interests, sources] = await Promise.all([
          coreQuery(sql, id), buildingsQuery(sql, id), timelineQuery(sql, id),
          interestsQuery(sql, id), sourcesQuery(sql),
        ]);
        if (!core.length) return cors(json({ error: "property not found" }, 404));
        body = buildProfile(core[0], buildings, timeline, interests, sources);
      }
      const resp = json(body);
      resp.headers.set("Cache-Control", `public, max-age=300, s-maxage=${CACHE_SECONDS}`);
      ctx.waitUntil(cache.put(cacheKey, resp.clone()));
      return cors(resp);
    } catch (e) {
      return cors(json({ error: "profile service error", detail: String(e && e.message) }, 500));
    }
  },
};
