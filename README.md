# Pulaski County Building Map

An interactive, locally-hosted map of **every building footprint in Pulaski County, Arkansas**
(225,774 structures), colored by **year built, building type, stories, size, or value** —
in the spirit of [Colouring London](https://colouringlondon.org), the Amsterdam building-age
map, and "All the Buildings in Manhattan".

## Quick start (data already built)

```bat
python serve.py
```

Open **http://localhost:8080**. That's it — the app is fully static
(`web/` + `web/data/buildings.pmtiles`) and works offline except the optional
online basemaps.

> Why not just open `index.html`? PMTiles needs HTTP **Range** requests, which
> `file://` and Python's stock `http.server` don't support. `serve.py` adds them.

## Controls

| Control | What it does |
|---|---|
| **Search** | Owner name or street address across all ~180k parcels — flags every property of an owner, flies to addresses; building popups show the owner (click it to see their other properties) plus deed/assessor/tax lookup links |
| **Color by** | Year built · Building type · Stories · Building sq ft · Footprint area · Improvement value · Vehicles at address · Personal property value |
| **Palette + flip** | Colouring-London, Amsterdam-fire, Viridis, Magma, Turbo, Cividis, Cool-Warm |
| **Year built filter** | Range sliders + "include undated" |
| **Building types** | Toggle chips (single-family, commercial, condo, mobile…) |
| **Main buildings only** | Hides garages/sheds (largest footprint per parcel wins) |
| **3D height** | Extrudes by assessor story count (± exaggeration) — try pitch + rotate (right-drag) |
| **Basemap / background** | Pure black default; optional CARTO dark / light / OSM raster underlay |
| **Find vehicles** | Search assessor personal-property vehicles by make, model, and/or year; matches pin to its situs address |
| **H key** | Hide/show the panel |
| Hover / click | Tooltip / pinned popup with address, year, type, size, value |

## Rebuilding the data from scratch

```bat
pip install -r requirements.txt
python pipeline/run_all.py
```

Paths and rotating source URLs are centralized in `pipeline/common/settings.py`;
set `PULASKI_DATA_ROOT` to keep the (gitignored) data tree on another drive
(defaults to `./data` — all knobs documented in `.env.example`). Run the test
suite with `pip install -r requirements-dev.txt && python -m pytest tests/`.

Steps (each restartable, ~20–40 min total, ~2 GB temp disk):

1. `download_layer.py` — paginated GeoJSON pulls of PAgis **Building** (layer 21) and
   **Parcel** (layer 68) from `pagis.org/arcgis/rest/services/MAPS/BaseMap/MapServer`.
2. CAMA zip — Pulaski County Assessor raw **Real Property export** (Dropbox link on
   [the assessor's raw-data page](https://pulaskicountyassessor.net/services/raw-data-export/);
   set `PULASKI_CAMA_URL` if it rotates — see `.env.example`).
3. `build_cama_attrs.py` — per-parcel `year_built / stories / sqft / category` from
   `Residential_Buildings`, `Commercial_Sections`, `MobileHomeData`, `Improvements`,
   `UseCodesForParcels` (pipe-delimited, latin-1, dashes row 2).
   Residential `StoryHeight` is a code — mapping validated against second-floor area.
4. `join_buildings.py` — CAMA → parcel polygons (normalized parcel-number key, 84% match)
   → buildings via representative-point spatial join (89.7% of buildings dated).
5. `extract_pp.py` + `enrich_pp.py` — personal property (vehicle) exports
   (`PP_Dump1/2.xlsx` from the same assessor page): stream 1.7M rows, keep each
   account's latest assessment (≥2025, not CLOSED), count vehicles
   (Vehicle Type 1–98 with real make/model; business equipment excluded),
   sum assessed value, and join to buildings by normalized situs address
   (81% of PP addresses match; 67% of buildings get vehicle data).
6. `make_tiles.py` — pure-Python vector tiler → `web/data/buildings.pmtiles`
   (z9–z15, tiny-building dilation at low zooms so every house stays a visible speck)
   + `web/data/config.json` (stats, histograms, domains for the UI).
7. `build_vehicle_index.py` — parses the per-building `veh` strings into
   `web/data/vehicles.json`: one representative footprint per normalized address,
   interned makes/models/cities, and a flat table the browser filters client-side.
   Up to 6 vehicles are indexed per address, so busy apartment/dealer lots are only
   partially covered.
8. `build_owner_index.py` — streams the PAgis parcel layer (owner name, situs
   address, subdivision/lot/block, values, centroid — no bulk file kept on disk)
   → `web/data/owners.json` (the in-app owner/address search index) and
   `data/processed/parcel_owners.pkl` (parcel crosswalk seed for the
   recorded-documents roadmap, see `docs/recorded_documents_plan.md`).

## Public dispatch overlay

[dispatch.yml](.github/workflows/dispatch.yml) runs every ~15 minutes: it pulls the
City of Little Rock public CAD feed (`/pub/Home/CadEvents`), dedupes by
`hash(type+location+time)`, categorizes call types, geocodes against a PAgis
address-point index (`pipeline/build_addr_index.py`, ~97% match), and appends
JSONL archives to the **`data` branch**, republishing:

- `dispatch/out/recent_24h.geojson` — points (sensitive call types excluded)
- `dispatch/out/recent_7d.geojson` — bare points for the heatmap
- `dispatch/out/grid_30d.geojson` — ~500 ft cells with per-category counts
- `dispatch/out/stats.json` — totals + collection start date

The map fetches these from `raw.githubusercontent.com` (no Pages redeploy per
collection). Privacy rules follow the project plan: exact points only for the
last 24 h, aggregates beyond that, medical/welfare/death call types never shown
as points, and calls-for-service language throughout (a dispatch is not a
confirmed crime). Note: GitHub disables cron workflows after ~60 days without
repo activity — any commit re-enables it.

## Recorded documents collector

The same workflow also runs `pipeline/deeds_collect.py`: two gentle queries
per run against the Pulaski County Circuit/County Clerk's public
recorded-document index (pulaskideeds.com) — one recording-day × one
instrument-type group at a time (the server allows ~150 result rows per
query). Documents (deeds, mortgages, releases, liens, plats…, with grantor/
grantee names and structured legal descriptions) accumulate in
`deeds/raw/*.jsonl` on the **`data` branch**, are matched to parcels through
a subdivision/lot/block crosswalk (`pipeline/build_legal_index.py`), and
publish as `deeds/out/recent_activity.geojson` + `stats.json`. Harvest
currently covers recordings from 2026-04-01 forward (the clerk's verified
index lags recording by ~2–4 weeks). Design, source recon, and roadmap:
[docs/recorded_documents_plan.md](docs/recorded_documents_plan.md).
Military discharges and medical-record authorizations are never collected.

## 311 service-request overlay

The same cron also runs `pipeline/sr311_collect.py` against the City of
Little Rock's public 311 citizen portal (Motorola CWI,
littlerock-cwiprod.motorolasolutions.com). The portal's list API is public
and paginated but only exposes requests **updated in the last ~30 days**
(~16k rows), so the collector accumulates history on the **`data` branch**
(`sr311/raw/*.jsonl`, one line per observed request version) — seeded
2026-07-11 with the full window then extended a few hundred rows per day.
Opened/closed dates are *observed*: a request first seen with an open status
was just created (older untouched requests can't enter the update window),
and the update that moves it to a closed status is its closure. Outputs
(`sr311/out/requests.geojson` + `stats.json`) geocode ~96% of requests via
the shared PAgis address index. The map gets a 311 overlay (9 category
chips, open-only filter), building popups show the address's request
history with opened/closed dates, and "Color by → 311 requests at address"
shades buildings by accumulated request count (address joins render from
z13 up, where tiles carry address strings). Citizen-submitted free text and
photos are never collected — only type, status, dates, channel, and address.

## Permit overlay

`pipeline/build_permits.py` normalizes the City of Little Rock
[Planning & Development permits CSV](https://littlerock.gov/government/mayors-office/initiatives/city-of-lr-data/)
(2019 – present, ~63k permits after fee-row dedupe and void filtering) into
`web/data/permits/permits.geojson`: 11 derived categories (new construction,
addition, remodel/repair, demolition, roofing, trades, unsafe/vacant, sign,
other), issue dates, declared values, statuses — geocoded via the PAgis address
index (97.9%). Contractor/applicant names are deliberately excluded. The map
gets a permit overlay (year / type / min-value filters) and building popups
show an address-matched permit timeline. To refresh: grab the newest CSV link
from the city page (the filename is date-stamped), save as
`data/raw/lr_permits.csv`, rerun the script, commit. North Little Rock permits
are deferred to Phase 4: their WP File Download portal exposes monthly CSV/XLSX
reports, but those side-by-side report tables still need a dedicated parser.

## Deed activity overlay

The map can also show recent Pulaski County deed-index activity from the
`data` branch:

- `deeds/out/recent_activity.geojson` - matched deed document points
- `deeds/out/stats.json` - collection totals, earliest document date, match rate

The frontend reads those files from `raw.githubusercontent.com`, so new deed
outputs can appear without a Pages redeploy. Building popups show a recent
recorded-documents timeline by matched address; overlay point popups show
document type, matched address, record date, document number, and match quality
without grantor/grantee names. The current dataset is a seeded harvest; the
recurring collector is still a follow-up item.

## Data notes & caveats

- Assessor attributes are **parcel-level**: every structure on a parcel inherits the
  parcel's primary-improvement year/type (a 2005 garage next to a 1940 house shows 1940).
  "Main buildings only" filters to the largest footprint per parcel.
- ~10% of footprints have no assessor match (exempt land, rights-of-way, new splits,
  common areas) → rendered gray as *unknown*.
- Categories: `sfr, condo (HPR), plex (2–4 units), mobile, commercial/apartments,
  exempt/public, outbuilding` — derived from CAMA improvement descriptions.
- Vehicle counts aggregate by street address: apartment complexes sum all residents'
  vehicles; dealer/leasing lots reach hundreds. "Personal property value" includes
  business equipment; vehicle counts do not.
- Owner names come from the public county parcel roll (PAgis/assessor); they can
  lag recent sales and appear exactly as recorded (trusts, LLCs, co-owners).
  Building popups link to the official deed, assessor, and treasurer lookups —
  the roadmap for pulling recorded documents (deeds/mortgages/liens) into the
  map itself is in [docs/recorded_documents_plan.md](docs/recorded_documents_plan.md).
- Sources: **PAgis** (footprints, parcels, addresses, owners) · **Pulaski County
  Assessor** CAMA real property + personal property exports (public records) ·
  not an official record.

## Hosted version

Live at **https://brandongrant.github.io/pulaski_building_map/** — deployed by
[GitHub Actions](.github/workflows/pages.yml), which publishes `web/` to GitHub
Pages on every push to `main`. The app is fully static (no build step, no API
keys); GitHub Pages serves the Range requests PMTiles needs. To update the map,
re-run the pipeline locally and push the regenerated
`web/data/buildings.pmtiles` + `config.json`.
