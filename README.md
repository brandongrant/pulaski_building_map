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

[dispatch.yml](.github/workflows/dispatch.yml) runs hourly: it pulls the
City of Little Rock public CAD feed (`/pub/Home/CadEvents`), dedupes by
`hash(type+location+time)`, categorizes call types into ~20 buckets, geocodes
against a PAgis address-point index, and appends JSONL archives to the
**`data` branch**, republishing:

- `dispatch/out/recent_24h.geojson` — points, last 24 h
- `dispatch/out/recent_7d.geojson` — bare points for the heatmap
- `dispatch/out/grid_30d.geojson` — ~500 ft cells with per-category counts
- `dispatch/out/all.geojson` — every geocoded call, all-time (indefinite)
- `dispatch/out/stats.json` — totals, geocode-quality breakdown, per-category counts

The same workflow also collects the LRPD **daily incident reports** and rebuilds
the **Pulse** summary at the end of every run — see
[Daily police reports](#daily-police-reports--the-pulse-tab) below.

**Geocoding (verified-address, fixed 2026-07-13):** the location string is
canonicalized so street-type/direction synonyms match the address index
("CHENAL PKY" → "CHENAL PKWY"), looked up exactly, then interpolated by house
number along the street (`pipeline/build_addr_index.py` now streams PAgis
address points with house numbers into the street index). A call is pinned only
at a *verified* position — an exact match, a house-number interpolation, or a
real intersection; anything else is counted but not placed. The old
street-centroid fallback is gone: it used to drop every un-matched call on a
street onto one averaged point, which read as a phantom hotspot at the wrong
address. Outputs are re-geocoded from the archived location string on every run,
so this fix (and future ones) re-scores all history. ~98% of calls place.

The map fetches these from `raw.githubusercontent.com` (no Pages redeploy per
collection). Calls-for-service language throughout (a dispatch is not a
confirmed crime, report, or arrest). **Display policy:** as of 2026-07-13 the
site owner opted to map every call type as a precise point, indefinitely,
including medical/welfare/mental-health/death/sex/domestic calls; the collector
still flags those (`sens`) to show a "sensitive call type" note in the popup.
Rebuilding the index: `python pipeline/build_addr_index.py` (streams PAgis, no
raw file), commit the refreshed `address_index.json.gz` to the `data` branch.
Note: GitHub disables cron workflows after ~60 days without repo activity — any
commit re-enables it.

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

## Daily police reports → the **Pulse** tab

The site has two views, switched from the pill at the top: **Map** (everything
above) and **Pulse** — the city's crime picture as a shape rather than a list.

LRPD publishes a PDF of complete incident reports each weekday and takes the old
ones down after about a week, so the archive only exists if something keeps
checking. [`reports_collect.py`](pipeline/reports_collect.py) runs inside the
same hourly workflow as the dispatch collector but self-throttles to one
listing fetch every four hours: it scrapes the
[daily reports page](https://littlerock.gov/residents/police-department/21st-century-policing/daily-reports/),
downloads dates it has not seen, and parses each packet with
[`lrpd_reports.py`](pipeline/lrpd_reports.py) — a geometric reader of LRPD
Form 5501 (field boxes are fixed to the point; the red "Redact Before Release"
stamp is filtered out by colour before words are assembled). Roughly a third of
the PDFs are scans with no text layer; whatever is recoverable from those is
kept and marked `partial`.

**What is kept, and what is not.** The published record is incident-level only:
number, date/time, call type, statutory offenses, district, address, plus the
dispatch category and a geocode. The narratives name victims, witnesses and
suspects — those paragraphs are read to derive mechanical tags (*firearm,
forced entry, property taken, suspect fled, arrest made*…) and then discarded.
Nothing person-level is written to disk or served, which
[`tests/test_lrpd_reports.py`](tests/test_lrpd_reports.py) pins against
`reports_collect.PUBLISH_FIELDS`. Every case links straight to the city's own
PDF page for anyone who wants the full document.

[`build_pulse.py`](pipeline/build_pulse.py) then folds the dispatch archive, the
daily-report incidents and the 2017–2025 offense export into a single ~55 KB
`pulse/out/pulse.json` on the `data` branch, so the tab draws instantly instead
of grinding through a 3 MB GeoJSON in the browser. Everything is bucketed in
America/Chicago local time.

The tab itself ([`web/pulse.js`](web/pulse.js), hand-built SVG, no chart
library) is organised around time rather than place:

| Panel | What it shows |
|---|---|
| **The clock** | A 24-hour dial: outer ring = every call, inner ring = shots/assault/robbery, each scaled to its own busiest hour. The city is busiest mid-afternoon and most violent near midnight — the two rings make that a shape, not a statistic. |
| **The week** | 168 squares, one per weekday-hour, so the worst square on the board is obvious. |
| **What** | Category leaderboard with last-7-days counts and the change against the week before. Click one to filter every other panel. |
| **Where** | An abstract hex mosaic — quarter-mile cells, no basemap, no labels, just the city's shape drawn out of where officers were sent. Click a cell to open it on the map. |
| **Corridors** | The street names that keep coming up, split by category. |
| **Risk assessment** | Per-location statements — *"higher risk of theft at Walmart (2700 S Shackleford) on Friday, most likely between 2 and 4 PM"* — built from the long offense record and current calls for service together. Clicking one flies the map to it at building zoom with the dispatch points switched on. |
| **Case files** | The parsed daily reports as cards: offenses, event tags, address, and a link to the city's PDF. |
| **The long view** | Month-of-year shape of 115k reported offenses over the complete years of the LRPD export. |

### How a risk statement is built

Three datasets with opposite gaps. LRPD's published offenses reach back to 2017
and carry a date but **no time of day**; the live dispatch archive has real
timestamps and is current, but is weeks long and records *calls for service*
rather than confirmed offenses; the daily incident reports are richer and rarer
still. Each statement is assembled from whichever of them the location has:

- **Where** — offenses grouped by LRPD's own recorded address. A place with no
  offense record at all still qualifies on ≥14 reportable calls for service, so
  somewhere busy *now* appears alongside somewhere with a long history.
- **What / when (day)** — that location's own record, expressed as a *lift*: how
  much likelier the offense is on its peak day than on an average day there.
- **When (hour)** — that location's own dispatch calls once there are ≥25 of
  them; until then the citywide clock for that offense, and the card says so.
- **Which business** — nearest named footprint from PAgis's building layer
  (`pipeline/build_place_index.py`, 11.7k named buildings) within **45 m**.
  Beyond that the match drifts onto a neighbour, so the card falls back to the
  bare address. Per-building suffixes are stripped (`Fair Oaks Apts - Bldg 8` →
  `Fair Oaks Apts`) because nearest-match picks an arbitrary building inside a
  complex.

Two details make the merge honest rather than tidy:

- **Current-only places qualify on reportable categories, not raw call volume.**
  Rank by total calls instead and the board fills with the county jail (prisoner
  transports logged as `assist`) and a homeless shelter (welfare checks) —
  places the police are sent *to help*.
- **Rates are normalised before ranking.** Citywide, reportable calls run ~4×
  more frequent than reported offenses (`call_to_offense` in `pulse.json`), so
  an unscaled rate would push every call-based entry above every offense-based
  one. Cards built from current calls are tinted and labelled as such.

The section carries its own caveat, and it is worth repeating here: these are
**concentrations, not personal odds**. There is no footfall denominator — a busy
store records more offenses partly because far more people walk through it — and
a single address can absorb a whole parking lot or shopping centre.

`web/data/pulse/pulse.json` is a committed snapshot so the tab works on a fresh
clone; the live file on the `data` branch is preferred and the page says so when
it has to fall back. `web/data/places.json` is likewise committed and read at
build time — the hourly workflow does **not** rebuild it; refresh it by hand with
`python pipeline/build_place_index.py` when PAgis republishes its buildings.

For local iteration:

```bat
python pipeline/build_pulse.py --store <data-branch-checkout> --out web/data/pulse/pulse.json
```

then open `http://localhost:8080/?pulse=local`, which skips the published
summary and reads the checkout's own copy.

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

## Reported crimes 2017–2025 (inside all-time points)

`pipeline/build_crime.py` turns a bulk **LRPD incident-statistics CSV** (index /
Part-I offenses — violent + property — 2017 to Feb 2025) into a compact, interned
flat table `web/data/crime/crimes.json` (114,742 points with addresses,
~1.5 MB gzipped on the wire — same client-expanded approach as `vehicles.json`,
not a heavy GeoJSON). The CSV already carries LRPD's own
`LATITUDE`/`LONGITUDE`, so no geocoding is needed; incidents LRPD suppresses
the location of (all RAPE rows, plus a few thousand others — ~6k total) are
counted but not plotted. Each offense is categorized with the **dispatch
taxonomy** (assault / robbery / sex / burglary / theft).

On the map these historical offenses live **inside the dispatch overlay's
"all-time points" mode**: the browser merges them with the live
calls-for-service archive into one layer, so they behave exactly like the newer
dispatch records — same category chips, same colors, same click popups (offense,
address, date, LRPD clearance status, weapon), plus a year-range slider
(2017 → now) that filters the combined layer. A reported offense is not a
conviction. To refresh: drop a newer LRPD export at `data/raw/lrpd_crime.csv`
(or pass `--csv`), rerun the script, commit `web/data/crime/`.

## Watch — surveillance devices and their paper trail

The **Watch** tab maps every surveillance device in Pulaski County this project
can source, says what each kind of device actually does, and links it to the
records that paid for it.

```bat
python pipeline/build_surveillance.py
```

Three kinds of source are merged into `web/data/surveillance/`:

| Source | What it gives |
|---|---|
| [ARDOT's published camera layer](https://layers.idrivearkansas.com/cameras.geojson) | ~190 state traffic cameras in the county, with model and live stream link |
| LRPD's own reader list (FOIA `PDFOIA-2025-4004`) | all 116 Flock plate readers, geocoded against the PAgis locator |
| OpenStreetMap `man_made=surveillance` (the DeFlock tagging, ODbL) | plate readers and gunshot sensors nobody published |
| `pipeline/surveillance/sightings.json` | devices photographed from the road, each scored against the published list |

A reader and a volunteer-mapped point within 70 m are treated as the same pole,
so nothing is counted twice; the authoritative record keeps the pin and inherits
the direction the volunteer recorded. Each field sighting carries an explicit
confidence (`confirmed` / `likely` / `probable` / `uncertain`), the reasoning
behind the identification, and what evidence would settle it — several are
deliberately filed as *not identified*.

### The paper trail

`pipeline/surveillance_docs.py` turns a public record into a linked entry:

```bat
python pipeline/surveillance_docs.py add <url-or-file> --program flock-lrpd
```

It fetches the document, extracts the text (PDF, HTML or plain), and pulls out
the dollar figures, resolution numbers, account codes, cooperative-contract
numbers and vendor names *as literal strings that appear in it*. The extracted
text is committed to `pipeline/surveillance/doc_text/` next to the entry, so any
figure shown in the app can be checked against its source. `list` shows the
trail, `rebuild` re-extracts offline, and `queue <file>` ingests submissions
exported from the site's own "add to the trail" form.

Nothing is summed into a headline total: renewals and amendments overlap, so the
page shows authorizations as a sequence of decisions instead.

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
