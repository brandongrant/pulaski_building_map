# Phase 1 setup — canonical database + property-profile API

One-time setup for the Phase 1 stack (roadmap §6): a Neon Postgres/PostGIS
database holding the canonical property model, loaded from artifacts this
repo already produces, served by a small Cloudflare Worker, surfaced in the
map as a property-profile drawer.

## 1. Create the database (you, ~2 minutes)

1. Sign up / sign in at **https://neon.tech** (free plan — 0.5 GB storage,
   100 compute-hours/month; this dataset uses roughly half the storage and
   the API's usage rounds to nothing).
2. Create a project (any name, e.g. `pulaski`; region: AWS us-east works).
3. On the project dashboard press **Connect** and copy the **connection
   string** (`postgresql://…neon.tech/neondb?sslmode=require`).
4. Locally: create a file named `.env` in the repo root (it is gitignored)
   containing one line:

   ```
   PULASKI_DATABASE_URL=postgresql://…your string…
   ```

No manual SQL needed — the loader creates the extensions and schema.

## 2. Load the data

```bat
pip install -r requirements.txt      (adds psycopg)
python pipeline/load_db.py --env-file .env --dry-run    (sanity: parses inputs only)
python pipeline/load_db.py --env-file .env --limit 500  (optional smoke load)
python pipeline/load_db.py --env-file .env              (full load, a few minutes)
```

Set `PULASKI_DATA_ROOT` if the data tree lives outside the checkout. The
loader is idempotent — rerun it any time to refresh (deterministic ids mean
profile URLs survive reloads). It prints table counts and total database
size at the end; expect ~180k properties, ~226k buildings, ~79k events.

## 3. Deploy the profile API (you run the commands; needs your Cloudflare login)

```bat
cd worker\profile-api
npm install
npx wrangler login              (once per machine)
npx wrangler secret put DATABASE_URL     (paste the same connection string)
npx wrangler deploy
```

Copy the printed `https://pulaski-profile.<subdomain>.workers.dev` URL into
`web/data/services.json` as `services.profile_api` (no trailing slash),
commit, push. Smoke test:

```
curl https://pulaski-profile.<subdomain>.workers.dev/v1/properties/43L-109.00-373.00
```

## 4. What it enables

- Building popups gain **"View property profile →"** — a drawer with
  overview, valuation metrics, buildings, one merged chronological timeline
  (permits + recorded documents + 311), sources, and warnings.
- Shareable links: `…/#map=…&property=<id>`.
- Refresh cadence: rerun `load_db.py` whenever the underlying artifacts
  refresh (owner index, permits CSV, collector outputs). The Worker caches
  profiles for 6 h.

## Notes / deviations from the roadmap draft

- Building footprint polygons stay in PMTiles (the DB stores centroids +
  attributes) to fit the free storage tier; `building.geometry` is nullable.
- Parcel polygons aren't loaded in Phase 1 (raw layers aren't retained
  locally); `property.centroid` comes from the owner-index representative
  point.
- Dispatch calls are **not** loaded — the project's privacy policy limits
  them to 24-h points and aggregates, which a permanent per-property history
  would violate.
- The API is a Cloudflare Worker (JS) rather than the roadmap's sketched
  Python service: it reuses the deployed-Worker pattern from the deeds
  proxy, adds no new vendor, and Phase 2's query engine can still choose a
  different runtime later.
