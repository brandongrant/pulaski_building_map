# Session handoff — owner search + recorded-documents buildout

Written 2026-07-06 (evening). Read this top-to-bottom before touching code;
it encodes a full day of reverse-engineering you should not repeat.

## 2026-07-07 sixth follow-up — deeds by LEGAL DESCRIPTION (major fix)

Shipped to `origin/main` (`5d6f321`). Fixes the user's report that some deed
clicks (e.g. 18 TOULOUSE CT, owner "WEBSTER FAMILY LIVING TRUST") showed an
error or no documents.

ROOT CAUSE: the popup searched PulaskiDeeds by the ASSESSOR OWNER NAME. That
string frequently has no matching clerk party entity — the clerk indexes
names by surname/first-token prefix ("WEBSTER" → 271 parties incl.
"WEBSTER FAMILY TRUST", "WEBSTER LIVING TRUST", but NOT the assessor's exact
"WEBSTER FAMILY LIVING TRUST"), so `last_name=<full owner string>` returns 0
rows → the popup dance lands on an empty/error page.

THE FIX — search by legal description instead (owner-independent). NEW
hard-won facts about PulaskiDeeds' `property` search (all measured live):
- It is a plain **top-level POST to `content.php`** with fields
  `searchType=property, LOT, BLC, RNG, SEC, QTR, SUB, TWP, PD, TRCT, UNIT,
  BLD, PH, CON, prop_start_date=01/01/1903, prop_end_date=<today>` + every
  `instType[<code>]=<code>` (+ `instType[ALL]=ALL`). It renders results
  **from the POST response itself** — NO storeDataString, NO storeEID, NO
  pick list, NO `random` token needed. This is far simpler than the name flow.
- Session requirement: exactly ONE prior `GET index.php` (sets county/state).
  NO `Accept` POST needed. A bogus/absent `?<random>` is fine. With no prior
  index.php at all you get the 46-byte "County and/or state…" error.
- `SUB` matches as a **case-insensitive PREFIX** against the clerk's OWN
  subdivision vocabulary (6,885 options, saved to
  `pipeline/pulaski_subdivisions.json`). The clerk spells names differently
  from the assessor: clerk "ST CHARLES ADN" vs assessor "ST CHARLES ADDN";
  "SAINT CHARLES ADN" and a trailing space both return 0. Sending the
  shortest clerk SUB for a base name also catches its phased siblings
  ("ST CHARLES ADN" prefixes "ST CHARLES ADN PH VIII") without leaking into
  "ST CHARLES PLACE".
- Results carry ONE ROW PER PARTY SIDE (same as instrumenttype) — merge by
  instrument number. Column 3 is the structured legal (SUBDIVISION/LOT/BLOCK
  or "PROPERTY DESCRIPTION:LT …").

`pipeline/pulaski_legal.py` (`SubResolver`) maps assessor subdivision →
clerk SUB via stripped-base matching; 180,227/180,230 parcels resolve.
`build_owner_index.py --rebuild-web` regenerates `owners.json` from the
existing `parcel_owners.pkl` (NO re-download) with a shared `subs` string
table + per-property `[…, parcelId, subIdx, lot, block]` (entry indices
5,6,7,8). owners.json grew 12.5 → 18.4 MB (~5-6 MB gzipped on the wire).

Client: `app.js` `parcelAtAddr` now returns `{id,owner,value,sub,lot,blc}`;
`openPulaskiProperty` / `deedPropertyLink` (data-pulaski-sub/lot/blc) drive
the property POST through the SAME first-party named-popup mechanism the
inst/owner flows use — `window.open(deeds-open.html)` → at
`PULASKI_SPINNER_MS` navigate popup to `index.php` → at +1600 ms POST the
property form to `content.php` (target = popup name). `recordLinks` prefers
this legal search when `parcel.sub && parcel.lot`, else falls back to the
owner-name search, else the bare site. `deeds-open.html` now shows the legal
context AND can self-drive (property/inst/owner) when opened standalone
(new tab / middle-click), since the opener isn't driving it then.

VERIFIED end-to-end WITHOUT a browser (verify_browser_path.py pattern): load
the shipped owners.json → replicate normAddrJS + parcelAtAddr + the JS field
list → POST live. 18 TOULOUSE CT → 27 docs; 14 TOULOUSE → 27; 3420 W 19TH ST
(HIGHLAND PARK lot 13 block 2, exercises BLC) → 23. STILL UNVERIFIED here
(hidden preview tab blocks window.open): the popup navigation end-to-end —
but it reuses the exact mechanism the fifth follow-up verified in Chromium.
NEXT SESSION: ask the user to click a deed link on the live site and confirm
the parcel's deed list loads. If it races, the single tunable is the
`+1600 ms` gap in `openPulaskiProperty` (index.php must finish first).

Deferred/known limits: ~3 parcels have no lot → fall back to owner search;
a handful of assessor subdivisions with embedded lot codes (e.g.
"BRADDOCKS BLVD L2") resolve to a bare-prefix that can under-match; the
prefix SUB can over-match sibling subdivisions sharing a base name (LOT
still constrains it). The local deed-collector archive + `deedsTimeline`
(recent_activity.geojson) are unchanged and still list individual recent
instruments with working detail deep-links.

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

## 2026-07-07 fourth follow-up - mixed deed click failures

User reported that some `deeds` clicks worked while others still showed
"County and/or state have not been set properly." The likely split is:

- normal map-click interception now works for both instrument links and owner
  links when the current `app.js` is loaded;
- any non-intercepted deed link, cached old page, right-click/open-new-tab, or
  similar path still landed on `pulaski-open.html`, whose hidden iframe
  initializer was known-broken.

Fix applied:

- `web/pulaski-open.html` no longer auto-posts through a hidden iframe or
  navigates the top-level tab to PulaskiDeeds.
- The helper now shows the requested instrument/owner and an `Open records`
  button. That button runs the same first-party named-popup POST sequence used
  by the map click handler.
- Owner-search final navigation timing was lengthened in both `web/app.js` and
  `web/pulaski-open.html` because PulaskiDeeds can take several seconds to
  finish the final `ajaxActions.php` post; navigating to `content.php` too soon
  can leave the popup on the AJAX endpoint or in an unset-session state.

Verified locally in Chromium:

- `pulaski-open.html?inst=2016038433` button opened the warranty deed detail
  and image-on-file section.
- `pulaski-open.html?owner=SON+HYE+JIN%2FGRANT+BRANDON` button opened the index
  list with `GRANT BRANDON`, `SON HYE JIN`, and `View Image` rows.

## 2026-07-07 fifth follow-up - deed loading screen + iframe ruled out

Shipped and LIVE on `origin/main` (`fcbf070`, fast-forwarded from the
`claude/sharp-payne-7096cd` branch). Clicking a deed/owner link no longer
opens a blank popup. The popup now opens FIRST to a new first-party
`web/deeds-open.html` spinner ("Opening Pulaski County records…", shows the
instrument #/owner + a "PulaskiDeeds is slow, ~10-15s, keep this window open"
note + a manual fallback link), THEN the same proven session dance runs in
that window, delayed by `PULASKI_SPINNER_MS` (1500 ms) so the notice is seen.

`openPulaskiDeed` / `openPulaskiOwnerIndex` in `web/app.js` now do
`window.open(pulaskiLoadingURL(kind, value), target)` then
`w.location.href = <BASE>index.php` at `PULASKI_SPINNER_MS`; every later
storeDataString/storeEID/content.php step is offset by the SAME 1500 ms, so
the index.php→POST session-establishment gap (~1100 ms) is preserved. The
anchor `href` still points to `pulaski-open.html` (button fallback for
non-intercepted / middle-click / new-tab opens) — unchanged on purpose.

HARD-WON FACT - do NOT re-attempt an iframe embed of PulaskiDeeds. Its
`PHPSESSID` is `Set-Cookie: …; path=/; Secure` with NO `SameSite`, i.e.
browser-default `SameSite=Lax`, so it is NOT sent on cross-site iframe
requests. `content.php` with no session returns the 46-byte "County and/or
state have not been set properly" body (measured live). That is exactly why
the earlier hidden-iframe attempt failed; a visible iframe would only show
that error. Framing itself is allowed (no `X-Frame-Options` / CSP), but that
is irrelevant given the cookie. Lax cookies ARE sent on top-level
navigations, so the first-party named popup is the ONLY path that carries the
session — keep it.

STILL UNVERIFIED (the verification env cannot do it — hidden preview tab
blocks `window.open`, no live PulaskiDeeds session): the popup dance end to
end. `deeds-open.html` rendering WAS verified live (real `#ctx`, spinner,
fallback link). NEXT SESSION: ask the user whether a real deed click shows
the spinner AND then loads the record. If the record stops loading, tune
`PULASKI_SPINNER_MS` down/up (1500 ms could race a slow index.php load).

Also done this session (research, IN CHAT ONLY — not saved to any file): an
audit of which Arkansas counties expose each dataset this map uses. Headline:
building footprints are statewide (all 75, AR GIS Office); parcels 62/75;
assessor + deeds are near-universal but as *lookup* via actDataScout /
ARCountyData (paid bulk via DataScoutPro); free bulk assessor + personal-
property exports are Pulaski-notable; genuine public police-dispatch feeds
exist only in Little Rock, Fayetteville, and Springdale. Washington County
(Fayetteville/Springdale) is the strongest county for replicating the FULL
map. Exact per-county vendor enumeration was cut short by a session limit.

Repo / worktree hygiene (matters for the next session):
- `origin/main` = `fcbf070` and contains ALL deed work + this loading screen.
  This supersedes the stale commit list in "Where you are" below.
- The MAIN checkout `D:\Claude Code Projects\Building_Map` is BEHIND
  `origin/main` and carries UNRELATED uncommitted edits (a mobile
  "tap map to hide panel" change in `web/app.js` + `.claude/launch.json`,
  plus untracked `pulaski_deeds_*source_code.pdf`). Not shipped — reconcile
  (`git fetch`/pull) or ignore before working there.
- `elegant-allen-733b4f` worktree holds SUPERSEDED deeds-UI work (deeds
  already shipped) — ignore or remove.
- `.claude/launch.json` in the main checkout gained an uncommitted
  `deeds-sharp-payne` config (port 8083) that serves the sharp-payne
  worktree `web/` for preview — convenient, not committed.

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
