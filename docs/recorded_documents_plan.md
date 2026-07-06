# Pulaski County Address-Based Recorded-Document Sources

Purpose: source-link reference for adding deed, mortgage, grantor, grantee, and
related recorded-document information to the address-based Pulaski County map.

Last reviewed: 2026-07-06 (user-provided spec; recon findings appended at bottom)

---

## Primary recorded-document sources

| Source | Direct link | Use |
|---|---|---|
| Pulaski County Deed / Recorded Document Search | https://pulaskideeds.com/search/index.php | Search recorded real-estate documents, including deed and mortgage-related records |
| Pulaski Deeds search / disclaimer page | https://pulaskideeds.com/search/ | Entry page and county disclaimer for the recorded-document search system |
| Pulaski County Circuit/County Clerk — Real Estate Dept | https://pulaskiclerk.ar.gov/departments/real-estate/ | Official county page for real-estate recording, document requirements, online research, record availability, and Property Fraud Alert |
| Pulaski County real-estate records order form | https://ar.accessgov.com/pulaski-circuit/ | Request/order real-estate records from the Circuit/County Clerk |
| Pulaski County eRecording portal | (see clerk real-estate page) | Electronic filing portal — recording-system reference, not a public archive search |

## Property / parcel / owner / assessment sources

| Source | Direct link | Use |
|---|---|---|
| Pulaski County Assessor | https://pulaskicountyassessor.net | Official assessor property-record source |
| Assessor raw data export | https://pulaskicountyassessor.net/services/raw-data-export/ | Raw assessor data downloads (already used for parcel/owner-level enrichment) |
| ARCountyData — Pulaski search | https://www.arcountydata.com/county.asp?county=pulaski&directlogin=true | Public Pulaski real-estate record search; includes grantor/grantee lookup entry points |
| PAgis parcel layer (BaseMap/68) | https://www.pagis.org/arcgis/rest/services/MAPS/BaseMap/MapServer/68 | OWNERNAME + situs address + SUBDIV/LOT/BLOCK + PARCELLGL per parcel — powers the map's owner index |
| Pulaski County GIS viewer | https://www.arcgis.com/apps/webappviewer/index.html (Pulaski parcel viewer) | Parcel/GIS cross-reference |
| Pulaski County Treasurer tax records | https://public.pulaskicountytreasurer.net/ | Property-tax lookup |

## Recorded document types to classify

| Category | Document types / keywords |
|---|---|
| Deeds | Warranty Deed, Quitclaim Deed, Special Warranty Deed, Beneficiary Deed, Correction Deed, Tax Deed, Commissioner's Deed, Trustee's Deed, Mineral Deed |
| Mortgages / security instruments | Mortgage, Deed of Trust, Security Instrument, Assignment of Mortgage, Mortgage Modification, Subordination Agreement |
| Releases / satisfactions | Mortgage Release, Deed of Trust Release, Satisfaction, Release Deed, Partial Release |
| Liens | Materialmen's Lien, Mechanic's Lien, Medical Lien, Federal Tax Lien, State Tax Lien, Judgment Lien |
| Foreclosure / default | Lis Pendens, Notice of Default, Notice of Trustee's Sale, Foreclosure Deed, Commissioner's Sale |
| Easements / access | Easement, Utility Easement, Drainage Easement, Access Easement, Right-of-Way |
| Plats / surveys | Plat, Replat, Survey Plat, Lot Split, Boundary Line Agreement |
| Leases / agreements | Lease, Memorandum of Lease, Land Contract, Agreement, Covenant, Restriction |
| Probate / estate / trust | Affidavit of Heirship, Executor's Deed, Administrator's Deed, Trust Deed, Certificate of Trust |
| Powers / miscellaneous | Power of Attorney, Revocation, Affidavit, Correction Instrument, Notary Bond |

## Search link map

| Search need | Use this link | Search by |
|---|---|---|
| Deed records | pulaskideeds.com/search/index.php | Document type, date, grantor, grantee, instrument number, book/page |
| Mortgage records | pulaskideeds.com/search/index.php | Document type, date, grantor/borrower, grantee/lender, instrument number |
| Grantor search | pulaskideeds.com/search/index.php | Grantor name / seller / borrower / releasing party |
| Grantee search | pulaskideeds.com/search/index.php | Grantee name / buyer / lender / receiving party |
| Property/parcel lookup | arcountydata.com (Pulaski) | Address, parcel ID, owner, subdivision, legal description |
| Assessor property lookup | pulaskicountyassessor.net | Address, parcel ID, owner |
| GIS parcel matching | PAgis layer 68 | Address, parcel, map selection |
| Official record request | ar.accessgov.com/pulaski-circuit | Instrument number, book/page, name, date, property description |

## Fields to extract per recorded document (`recorded_documents`)

```
id, source_name, source_url, official_document_url,
instrument_number, book, page, recording_date, execution_date,
document_type_raw, document_type_normalized,
grantor_raw, grantee_raw, grantor_normalized, grantee_normalized,
lender_name, trustee_name,
legal_description, subdivision, lot, block,
parcel_id, situs_address, city, state, zip,
consideration_amount, transfer_tax_amount,
page_count, image_available, document_image_url, search_result_url,
match_status, match_confidence, matched_property_id,
created_at, updated_at
```

## Property matching fields (`property_crosswalk`)

```
property_id, parcel_id, situs_address, normalized_address,
legal_description, subdivision, lot, block,
owner_name_assessor, owner_name_deed_latest,
latest_deed_instrument_number, latest_deed_recording_date,
latest_mortgage_instrument_number, latest_mortgage_recording_date,
gis_object_id, building_id, match_method, match_confidence
```

Recommended matching priority:

1. Parcel ID / APN
2. Situs address
3. Legal description
4. Subdivision + lot + block
5. Assessor sales record cross-reference
6. Grantor/grantee name + date + consideration amount
7. Manual review queue

## Address-based property timeline events (`property_timeline_events`)

```
event_id, property_id, event_date, event_type, event_title,
event_summary, source_name, source_url, source_record_id
```

Example event types: deed recorded, mortgage recorded, mortgage released,
lien recorded, easement recorded, plat/replat recorded (Pulaski Deeds / Clerk
records); assessor sale update (Assessor / ARCountyData); building permit
(LR permit data); improvement update (assessor data); dispatch summary
(long-term public dispatch archive).

## Display fields for map popup / address report

```
Latest deed date · type · instrument number
Latest mortgage date · instrument number
Latest release date
Grantor · Grantee · Lender/mortgagee · Trustee (if applicable)
Sale/consideration amount (if present)
Legal description
Official document link
```

## Direct link storage pattern

```json
{
  "source_name": "Pulaski Deeds",
  "search_page_url": "https://pulaskideeds.com/search/index.php",
  "search_result_url": "<exact result URL after search>",
  "official_document_url": "<exact official document/image URL if exposed>",
  "instrument_number": "<instrument number>",
  "recording_date": "<recording date>",
  "document_type": "<document type>",
  "grantor": "<grantor>",
  "grantee": "<grantee>"
}
```

## Implementation checklist

- [x] Use address/parcel lookup from assessor/GIS source
      (pipeline/build_owner_index.py → parcel_owners.pkl + web/data/owners.json)
- [x] Normalize party names (owner index normalizes for search)
- [x] Map name-search UI (owner + address search, panel search box)
- [ ] Query recorded-document source by parcel/name/date where available
- [ ] Extract document type, instrument number, recording date, grantor,
      grantee, legal description, source URL
- [ ] Normalize document type into deed/mortgage/release/lien/easement/plat/etc.
- [ ] Match documents to parcel/building (crosswalk priority above)
- [ ] Store exact official document URL when available
- [ ] Add document events to property timeline
- [ ] Add filters for document type, date range, grantor, grantee, lender,
      instrument number

## Source notes (from clerk / site disclaimers)

- The Clerk's Real Estate Department records deeds, mortgages, plats, liens,
  and leases; they become public records.
- Online research covers records from **1994 to present**; earlier documents
  require in-person research.
- Pulaski Deeds shows a county disclaimer: data provided as-is; the **Temp
  Index** is only a partial listing of documents currently being processed.

---

## Recon findings (2026-07-06, verified with live requests)

How pulaskideeds.com actually works — basis for the Phase-2 collector:

- **Session flow**: `GET /search/index.php` shows a legal disclaimer;
  `POST Accept=Accept` to the same URL sets the session cookie. All further
  requests need that cookie. A per-session `random` token is embedded in the
  search forms and echoed in query strings.
- **Search types** (forms on index.php, results at `content.php`):
  - `simplesearch` (GET): `name`, `start_date`, `end_date` (MM/DD/YYYY)
  - `name` (POST): `last_name`, `party_type` (grantor/grantee/both),
    `entity_type`, `exact_match`, date range, per-instrument-type checkboxes
    `instType[XXX]`. Name search is a **two-step entity flow**: it first
    resolves matching party names via `ajaxActions.php`
    (`action` + `last_name` + `entity_type`), user picks entities
    (`storeEID`), then an Index List of their documents is rendered.
  - `instrumenttype` (GET): date range + `instType[...]` checkboxes —
    **best harvest entry point** (sweep by recording-date window).
  - `bookpage`, `instnum` (GET): direct record lookups.
  - `property` (POST): `SUB` (subdivision, from a fixed pick list), `LOT`,
    `BLC`, `RNG/SEC/QTR/TWP`, etc. — **no street-address search exists**;
    property search is legal-description based, so the parcel crosswalk
    (SUBDIV/LOT/BLOCK from PAgis layer 68) is the join path.
- **Results**: server-rendered HTML table (`#results`, DataTables client-side,
  100 rows/page in DOM) with columns: Image, Recording Date, Party Type,
  Party 1, DStatus, Party 2, Doc Type, Book Info, Legal, XRef.
- **Per-document deep link** (works as `official_document_url`):
  `https://pulaskideeds.com/search/content.php?searchType=details&noImage=1&inst_num=<instrument>`
- **Temp Index** (`searchType=temped`, GET, no params) returns the in-process
  queue (200 rows observed, including same-day mortgages) — useful as a
  freshness probe.
- **No robots.txt** (404). No bulk export. A collector must therefore be
  polite: low request rate, date-window sweeps, off-peak scheduling, and an
  archive so windows are never re-fetched (same pattern as the dispatch
  collector on the `data` branch).
- Deep links into a *search result* are not shareable — the session/disclaimer
  gate means map popups should link the search entry page (done) until the
  collector exists.

### Phase plan

1. **Done — owner index + name search**: parcel owners/addresses searchable
   on the map; owner shown in building popups; records links per popup;
   `data/processed/parcel_owners.pkl` seeds `property_crosswalk`
   (parcel_id, situs, SUBDIV/LOT/BLOCK, legal, values, centroid).
2. **Collector** (GitHub Actions, like dispatch): daily `instrumenttype`
   sweep of the previous recording day (1994+ backfill optional, budgeted),
   parse the results grid → `recorded_documents` JSONL on the `data` branch;
   dedupe by instrument number.
3. **Crosswalk & match**: normalize legal descriptions; join documents →
   parcels by SUBDIV/LOT/BLOCK, then name+date fallback, per the matching
   priority; emit per-address latest-deed/mortgage fields + timeline events.
4. **Map integration**: popup "Recorded documents" timeline (like permits),
   grantor/grantee in the existing name search, document-type/date filters.
