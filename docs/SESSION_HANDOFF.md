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

## Where you are

Repo: `github.com/brandongrant/pulaski_building_map` (PUBLIC — Pages deploys
`web/` from `main`). Working branch **`claude/elegant-allen-733b4f`**
(worktree `.claude/worktrees/elegant-allen-733b4f`), two commits ahead of
main, **not merged**:

- `426b707` — owner/address name search (Phase 1)
- `d16704e` — pulaskideeds.com collector (Phase 2)

`data` branch (collector storage): seeded at `65aca5d` with
`deeds/legal_index.json.gz` + one harvested day (2026-05-15 deed groups,
62 docs). The dispatch cron commits to it every 15 min, so always
`git pull --rebase` before pushing there. A **stale local checkout** of it
exists at `D:\Claude Code Projects\Building_Map_data` — pull it before any
manual collector run, or ignore it.

Big gitignored inputs live in the MAIN checkout `D:\Claude Code
Projects\Building_Map\data\processed\`: `parcel_owners.pkl` (owner/legal
crosswalk seed), `legal_index.json.gz`, `buildings_final.pkl` (tiler input),
`cama_parcel_attrs.pkl`, `address_index.json.gz`. Disk is ~2 GB free on C:
and D: — never persist bulk downloads.

**Merging this branch to main is the gate for everything**: GitHub runs
scheduled workflows from the default branch, so the deeds collector only
starts collecting (2 queries / 15 min) after merge. Merge also publishes
owner search on the public site — user has approved building it, but let
the USER do or ask for the merge.

## What shipped this session

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

## THE NEXT TASK (agreed with user): click a building → see its deeds

User asked "what is required next so we can click to view deeds,
mortgages… when selecting a building" and was told step 3 below is the
build. Requirements already settled:

1. ~~Merge to main~~ (user's call; collector then fills Apr→Jul in ~4-5
   days at 192 queries/day vs ~870 pending day-groups).
2. Archive fills on its own.
3. **Build the map UI** (do this now; testable against the seeded day):
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
4. Later enrichment (not now): consideration/book-page need one details
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
