# Pulaski deeds proxy (Cloudflare Worker)

The map is a static GitHub Pages site, so it can't run the county's
recorded-document search and read the results back (browser cross-origin
security). This tiny Worker does that server-side and returns clean JSON the
map renders inline in the building popup — including the **current owner's
chain of title** (grantor → grantee), which owner-name search can't reach for
trusts/companies.

## What it does

`GET /deeds?sub=<clerk SUB>&lot=<lot>&blc=<block>` →

```json
{
  "sub": "ST CHARLES ADN", "lot": "373", "blc": "",
  "owner": ["WEBSTER CODY BRANDON", "WEBSTER DORIS TAYLOR", "WEBSTER FAMILY TRUST"],
  "since": 2006, "count": 5, "chain": 3,
  "docs": [
    { "date": "20220719", "type": "QUIT CLAIM DEED", "inst": "2022050990",
      "grantor": ["WEBSTER CODY", "..."], "grantee": ["WEBSTER FAMILY TRUST", "..."],
      "chain": true, "legal": { "LOT": "373", "SUBDIVISION": "ST CHARLES ADN" } },
    ...
  ]
}
```

It date-windows the property search to the last 20 years (fast — a handful of
rows) and fetches party names only for the most recent documents (the current
owner's chain is always recent). Results are cached 7 days, so the county site
is hit at most once per parcel per week.

## Deploy (one time, free)

You need a free Cloudflare account. From this `worker/` directory:

```bash
npx wrangler login       # opens a browser once to authorize
npx wrangler deploy
```

`wrangler deploy` prints the live URL, e.g.
`https://pulaski-deeds.<your-subdomain>.workers.dev`.

Then point the map at it: set `DEEDS_API` at the top of
[`web/app.js`](../web/app.js) to that URL (no trailing slash) and push. The
map's deeds link falls back to the plain PulaskiDeeds link whenever `DEEDS_API`
is empty or the Worker is unreachable, so nothing breaks if it's ever down.

Test it directly:

```bash
curl "https://pulaski-deeds.<your-subdomain>.workers.dev/health"
curl "https://pulaski-deeds.<your-subdomain>.workers.dev/deeds?sub=ST+CHARLES+ADN&lot=373&blc="
```

## Local test

```bash
npx wrangler dev --port 8791
curl "http://127.0.0.1:8791/deeds?sub=ST+CHARLES+ADN&lot=373&blc="
```

## Notes / limits

- **Politeness**: only parcels a user actually opens are fetched, cached a
  week. No bulk crawling. The Worker sends a descriptive User-Agent.
- **Coverage**: shows records from ~20 years back (covers the current owner).
  A parcel held longer than that may not show its purchase deed; the popup
  links out to PulaskiDeeds for the complete chain.
- **Current-owner detection** is a heuristic (grantee of the most recent
  ownership deed); messy title histories can mis-flag which documents belong
  to the current owner, but every document shown is genuinely recorded against
  the parcel's legal description.
- **No document images**: those stay gated behind the county's session; the
  popup links each instrument to its PulaskiDeeds page.
