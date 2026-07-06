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
| **Color by** | Year built · Building type · Stories · Building sq ft · Footprint area · Improvement value · Vehicles at address · Personal property value |
| **Palette + flip** | Colouring-London, Amsterdam-fire, Viridis, Magma, Turbo, Cividis, Cool-Warm |
| **Year built filter** | Range sliders + "include undated" |
| **Building types** | Toggle chips (single-family, commercial, condo, mobile…) |
| **Main buildings only** | Hides garages/sheds (largest footprint per parcel wins) |
| **3D height** | Extrudes by assessor story count (± exaggeration) — try pitch + rotate (right-drag) |
| **Basemap / background** | Pure black default; optional CARTO dark / light / OSM raster underlay |
| **H key** | Hide/show the panel |
| Hover / click | Tooltip / pinned popup with address, year, type, size, value |

## Rebuilding the data from scratch

```bat
pip install -r requirements.txt
python pipeline/run_all.py
```

Steps (each restartable, ~20–40 min total, ~2 GB temp disk):

1. `download_layer.py` — paginated GeoJSON pulls of PAgis **Building** (layer 21) and
   **Parcel** (layer 68) from `pagis.org/arcgis/rest/services/MAPS/BaseMap/MapServer`.
2. CAMA zip — Pulaski County Assessor raw **Real Property export** (Dropbox link on
   [the assessor's raw-data page](https://pulaskicountyassessor.net/services/raw-data-export/);
   update the URL in `run_all.py` if it rotates).
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
- Sources: **PAgis** (footprints, parcels, addresses) · **Pulaski County Assessor**
  CAMA real property + personal property exports (public records) · not an official record.

## Hosted version

Live at **https://brandongrant.github.io/pulaski_building_map/** — deployed by
[GitHub Actions](.github/workflows/pages.yml), which publishes `web/` to GitHub
Pages on every push to `main`. The app is fully static (no build step, no API
keys); GitHub Pages serves the Range requests PMTiles needs. To update the map,
re-run the pipeline locally and push the regenerated
`web/data/buildings.pmtiles` + `config.json`.
