# Session handoff — owner search + recorded-documents buildout

Written 2026-07-06 (evening). Read this top-to-bottom before touching code;
it encodes a full day of reverse-engineering you should not repeat.

## 2026-07-07 follow-up

Plain cold PulaskiDeeds detail URLs still fail with "County and/or state have
not been set properly", but the site accepts the same session sequence its UI
uses via GET requests: open `index.php`, call `ajaxActions.php` with
`action=storeDataString` and `dataString=searchType=details&inst_num=<inst>`,
then open `content.php?embedded=1&<cache-buster>`. `web/app.js` now uses that
sequence from a user click for instrument links. If no address-matched
instrument is in the current deed feed, the `deeds` public-record link falls
back to the owner's PulaskiDeeds index list by setting `storeEID` and the
pick-list `dataString`.

## 2026-07-07 second follow-up - exact public-record links

User tested the live GitHub Pages popup and found three public-record link
gaps:

- `deeds` for combined assessor owners such as `SON HYE JIN/GRANT BRANDON`
  opened `pulaski-open.html?owner=...`, then PulaskiDeeds
  `content.php?embedded=1&...`, and could land on HTTP 500 or an empty/bad
  result set. The manual PulaskiDeeds name search that works posts to
  `ajaxActions.php` with `searchType=name`, `start_date=01/01/1903`,
  today's `end_date`, `sort_type=Name`, `search_type=Standard`,
  `party_type=Both`, `entity_type=Both`, all instrument codes, and
  `plresults_length=100`.
- `parcel` and `assessor` both going to ARCountyData's exact
  `parcel.asp?County=Pulaski&ParcelID=<parcel>` page is acceptable for now.
- `taxes` was using the dashed/dotted parcel id in the Treasurer mobile URL
  (`53L-934.02-001.29`), which fails. The Treasurer mobile site accepts the
  compact parcel id (`53L9340200129`), but the user's desired bill/history/info
  pages in the classic Treasurer app are generated per session after selecting
  the parcel, so those `gsapdfs` / `PUBLIC.SEARCH` URLs should not be treated
  as permanent links.

Code state after this follow-up:

- `web/pulaski-open.html` now splits slash/semicolon owner strings into
  individual PulaskiDeeds party candidates, posts `storeEID` for each
  candidate, posts the fuller name-search `dataString`, and uses the last
  split owner as the primary `last_name` search term. For
  `SON HYE JIN/GRANT BRANDON`, that matches the user's successful manual
  `GRANT BRANDON` search while still including both `name[]` values.
- `web/app.js` now strips punctuation from Treasurer parcel ids before opening
  taxes and sends users to `web/tax-open.html`.
- `web/tax-open.html` shows the compact parcel, provides a working Treasurer
  mobile parcel-search link, links to the classic public tax app, and exposes a
  copy button for the compact parcel. This is a static-site compromise until a
  stable classic Treasurer bill/history URL can be proven.

## 2026-07-07 third follow-up - real click verification

User reported seeing no behavioral change. Live browser verification showed
why: `pulaski-open.html` was posting the PulaskiDeeds search through a hidden
third-party iframe, and Chrome did not carry that iframe session into the final
top-level `content.php` page. The visible result was still "County and/or state
have not been set properly" even though the static hrefs looked changed.

Fix applied after that report:

- `web/app.js` deed links now include `data-pulaski-owner` /
  `data-pulaski-inst`, so normal popup clicks are intercepted in the map page.
- The click handler opens PulaskiDeeds as a first-party named popup, then POSTs
  the `storeEID` / `storeDataString` forms into that named popup before sending
  it to `content.php`. This was verified locally in Chromium by clicking the
  generated `deeds` anchor for `SON HYE JIN/GRANT BRANDON`; the popup landed on
  the PulaskiDeeds index list with `GRANT BRANDON`, `SON HYE JIN`, and `View
  Image` rows.
- The old GET-style PulaskiDeeds URL helpers in `app.js` were removed so future
  work does not reuse the broken path.

Treasurer status from the same follow-up: the classic TaxPro app exposes a
`PostMenu()` / `WinOpen("rightframe","STDFIND","PUBLIC.SEARCH",...)` search
path after login, but the selected bill/history/info URLs remain session-
generated and were not proven stable enough to hard-link from GitHub Pages.
Keep `tax-open.html` as the current honest fallback unless a future pass
successfully reproduces the full TaxPro session state.

## Where you are

Repo: `github.com/brandongrant/pulaski_building_map` (PUBLIC - Pages deploys
`web/` from `main`). Current worktree:
`.claude/worktrees/sharp-payne-7096cd`, branch
`claude/sharp-payne-7096cd`.

Already pushed to `origin/main` before this exact-link follow-up:

- `426b707` - owner/address name search (Phase 1)
- `d16704e` - pulaskideeds.com collector (Phase 2)
- `b97f24c` - deed activity overlay and portable pipeline roots
- `08a93a0` - merge owner search and deed records UI
- `1ee9736` - exact Pulaski public record links, including owner/parcel data

Current follow-up edits, before the next commit: split combined PulaskiDeeds
owners in `pulaski-open.html`, normalize Treasurer parcel ids in `app.js`, add
`tax-open.html`, and update this handoff.

`data` branch (collector storage): seeded at `65aca5d` with
`deeds/legal_index.json.gz` + one harvested day (2026-05-15 deed groups,
62 docs). The dispatch cron commits to it every 15 min, so always
`git pull --rebase` before pushing there. A **stale local checkout** of it
exists at `D:\Claude Code Projects\Building_Map_data` — pull it before any
manual collector run, or ignore it.

Big gitignored inputs live in the MAIN checkout `D:\Claude Code
Projects\Building_Map\data\processed\`: `parcel_owners.pkl` (owner/legal
crosswalk seed), `legal_index.json.gz`, `buildings_final.pkl` (tiler input),
`cama_parcel_attrs.pkl`, `address_index.json.gz`. Disk was tight on C: and D:
during the original work - never persist bulk downloads.

The old merge gate is gone: owner search, deed collector, deed overlay, and the
first exact-link pass have already been pushed to `origin/main`.

## What has shipped

1. **Owner/address search** (`pipeline/build_owner_index.py` →
   `web/data/owners.json`, 12.5 MB; UI in `web/app.js` — `own` module,
   `initSearch`, `hit-ring` layer, popup Owner row + records links).
   Validated: 133,387 owners / 180,230 parcels; 100 % of addressed
   buildings resolve an owner (tile `addr`/`city` == parcel
   `ADRLABEL`/`ADRCITY`, joined via `normAddrJS`).
2. **Deeds collector** (`pipeline/deeds_collect.py`, wired into
   `.github/workflows/dispatch.yml` with `continue-on-error`): harvests
   pulaskideeds.com per (recording-day × type-group), archives to
   `deeds/raw/YYYY-MM.jsonl` on the data branch, matches docs to parcels
   (69 %) and publishes `deeds/out/recent_activity.geojson` + `stats.json`.
   Full recon + phase plan: `docs/recorded_documents_plan.md` (read it).

## Completed prior task - click a building and see its deeds

User asked "what is required next so we can click to view deeds,
mortgages… when selecting a building" and was told step 3 below is the
build. This is now historical context, not the next task:

1. ~~Merge to main~~ (user's call; collector then fills Apr→Jul in ~4-5
   days at 192 queries/day vs ~870 pending day-groups).
2. Archive fills on its own.
3. ~~Build the map UI~~ (shipped in `b97f24c` / `08a93a0`):
   - Fetch `https://raw.githubusercontent.com/brandongrant/pulaski_building_map/data/deeds/out/recent_activity.geojson`
     lazily (mirror `DSP_BASE` constant pattern in app.js) + `stats.json`
     for the section meta line.
   - Build a JS Map from normalized address → docs. Feature properties are
     ALREADY normalized to match tiles: `a` = normAddrJS-style address.
     Example: `{"d":20260515,"t":"WAD","c":"deed","dt":"Warranty Deed",
     "g1":"Brown Michael; Brown Shannon","g2":"Ms Newcon Llc",
     "a":"3301 S MARSHALL ST","n":"2026027112","mq":"prefix"}`
     Categories `c`: deed, mtg, rel, asgn, lien, fcl, plat, ease, oth.
   - `deedsTimeline(bldProps)` next to `permitTimeline()` in app.js;
     called in the building-popup `.setHTML(...)` chain. Row format like
     "2026-05 · Warranty Deed · Brown → Ms Newcon Llc · #2026027112".
   - **Cold deep links are blocked** (verified: details URL without a
     session returns 46-byte "County and/or state have not been set
     properly"). So per-doc rows should link the search entry
     `https://pulaskideeds.com/search/` and DISPLAY the instrument number
     (user accepts disclaimer once, pastes into Inst Num search).
   - Optional same pass: deeds overlay section (colored circle layer +
     type/date filters) cloning the permits section pattern (`PM_CATS`,
     `initPermits`, `pmSec` in index.html). If added: put the layer id in
     the popup `ovLayers` priority list and call
     `map.moveLayer("hit-ring")` after adding layers (search hits stay on
     top — see dspLoad/pmLoad).
   - Consider a `sendPrompt`-free equivalent of the permits fine-print:
     "Recorded-document index lags recording by 2–4 weeks · unofficial".
4. Later enrichment: consideration/book-page need one details
   request per doc; S/T/R fallback matching could recover part of the 31 %
   unmatched (rural metes-and-bounds); pre-Apr-2026 history should come
   from a Clerk bulk-export ask, not scraping (measured cost forbids).

## Hard-won facts (do NOT re-derive)

**pulaskideeds.com** (all measured live 2026-07-06):
- Session: `GET /search/index.php` → `POST Accept=Accept` (same URL) →
  cookie + per-session `random` token in the form HTML.
- A search = `POST ajaxActions.php {action:'storeDataString', dataString:
  <urlencoded form fields>}` **with header `X-Requested-With:
  XMLHttpRequest`**, then `GET content.php?embedded=1&<anything>` (same
  headers/session). Query params passed to content.php directly are
  IGNORED. `instType[ALL]` is NOT expanded server-side — send every code
  (`pipeline/deeds_inst_codes.json` has all 88, label↔code).
- Cost ~1 s per result ROW, hard ~180 s cap → 92-byte error body;
  load-dependent (identical query can fail then succeed — retry across
  runs, never loop). Keep any (day × group) under ~150 rows: that's what
  the `GROUPS` dict in deeds_collect.py encodes.
- Verified index lags recording 2–4 weeks; `searchType=temped` (no
  params) lists the in-process queue — good liveness probe. Results have
  ONE ROW PER PARTY SIDE (Party 1 = grantor side) — merge by `inst`.
- Politeness: no robots.txt, no bulk export, county-shared DB. Stay at
  ~2 queries/run. `DCH` (military discharge) and `ARM` (medical) are
  deliberately never requested.

**Matcher**: `deeds/legal_index.json.gz` tiers exact → base
(suffix/phase-stripped subdivision) → lb (lot|block buckets, unique
prefix-containment). Outputs are rebuilt from the raw archive EVERY run,
so matcher improvements re-score history retroactively;
`--max-queries 0` = pure re-match run (no network). Prefix tier was
validated by grantee == current parcel-roll owner on every spot-check.

**Web app** (`web/app.js`, vanilla JS, no build step): modules follow one
pattern — `pm` (permits), `dsp` (dispatch), `own` (owners); copy it.
`normAddrJS` is the universal address key. Popups: `featHTML` builds rows;
overlay clicks resolve via `ovLayers` list before buildings.
`fitBounds` MUST stay wrapped in try/catch (padding > map px throws
"Invalid LngLat (-Infinity, NaN)" on narrow windows — already fixed in
`setHits`, don't regress).

**Verification environment**: the Claude preview tab is
`visibility:hidden` → rAF never fires → MapLibre never loads styles/tiles,
screenshots time out, coordinate clicks no-op. Verify by calling app
functions directly via `preview_eval` (e.g. `searchRun("midark")`,
`selectResult("o", i)`, `featHTML({...}, false)`), check
`preview_console_logs`. Visual checks need the user's own browser at
http://localhost:8080 (`python serve.py`; launch config "building-map").

**Local collector test recipe** (used this session):
```
mkdir -p <scratch>/store/deeds
cp <main>/data/processed/legal_index.json.gz <scratch>/store/deeds/
python pipeline/deeds_collect.py --store <scratch>/store --max-queries 2 --start 2026-05-15
```

**Git**: gh CLI NOT installed; pushes work via Git Credential Manager
(user brandongrant). Commit messages end with the Claude Fable co-author
line. LF→CRLF warnings are normal. Data-branch pushes: worktree of
FETCH_HEAD → commit → `git push origin <tmp>:data` (raced cleanly with
cron this session).

## Roadmap after the deeds UI (user's larger plan)

grantor/grantee names folded into the map's name search · NLR permits
(needs browser-driven scraping of their WP File Download portal) ·
unsafe/vacant + rental-registry layers · dispatch trends/analytics ·
deeper deed history via Clerk bulk export. Memory file (machine-local):
`C:\Users\Brandon\.claude\projects\D--Claude-Code-Projects-Building-Map\memory\pulaski-building-map-project.md`.
