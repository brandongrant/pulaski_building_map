-- Phase 1 canonical property schema (roadmap §6.3), first migration.
-- Applied by pipeline/load_db.py, which records each file in schema_migrations.
--
-- Deviations from the roadmap draft, all storage-driven (Neon Free = 0.5 GB):
--   * building.geometry is NULLABLE and left empty — footprint polygons stay
--     in web/data/buildings.pmtiles (the map's source of truth); the database
--     carries centroids, attributes, and identity.
--   * property.geometry stays NULL in Phase 1 — parcel polygons aren't
--     retained locally (raw layers deleted for disk space); centroid comes
--     from the owner-index representative point.
--   * Loader supplies DETERMINISTIC uuid5 ids (namespace in load_db.py) so
--     re-loads upsert stably and profile URLs survive rebuilds; the
--     gen_random_uuid() defaults are only a safety net.

create extension if not exists postgis;
create extension if not exists pg_trgm;
create extension if not exists pgcrypto;

create table if not exists schema_migrations (
    filename text primary key,
    applied_at timestamptz not null default now()
);

create table data_source (
    source_id uuid primary key default gen_random_uuid(),
    slug text not null unique,
    name text not null,
    jurisdiction_id text,
    source_url text,
    source_owner text,
    entity_grain text not null,
    refresh_cadence text,
    sensitivity_class text not null,
    display_policy jsonb not null default '{}'::jsonb,
    terms_reviewed_at timestamptz,
    created_at timestamptz not null default now()
);

create table ingest_run (
    ingest_run_id uuid primary key default gen_random_uuid(),
    source_id uuid not null references data_source(source_id),
    code_commit text,
    started_at timestamptz not null,
    completed_at timestamptz,
    status text not null,
    source_effective_at timestamptz,
    source_etag text,
    source_sha256 text,
    record_count bigint,
    quality_report jsonb not null default '{}'::jsonb,
    error_summary text
);

create table source_record (
    source_record_id uuid primary key default gen_random_uuid(),
    source_id uuid not null references data_source(source_id),
    ingest_run_id uuid not null references ingest_run(ingest_run_id),
    source_key text not null,
    source_record_url text,
    observed_at timestamptz not null,
    effective_at timestamptz,
    payload_hash text not null,
    payload jsonb not null,
    is_deleted boolean not null default false,
    unique (source_id, source_key, payload_hash)
);

create index source_record_key_idx on source_record (source_id, source_key);

create table property (
    property_id uuid primary key default gen_random_uuid(),
    jurisdiction_id text not null,
    source_parcel_id text not null,
    parcel_id_normalized text not null,
    situs_address text,
    situs_address_normalized text,
    city text,
    state text,
    postal_code text,
    centroid geometry(Point, 4326),
    geometry geometry(MultiPolygon, 4326),
    first_seen_at timestamptz not null default now(),
    last_seen_at timestamptz not null default now(),
    unique (jurisdiction_id, parcel_id_normalized)
);

create index property_geometry_gix on property using gist (geometry);
create index property_centroid_gix on property using gist (centroid);
create index property_address_trgm on property using gin (situs_address_normalized gin_trgm_ops);
create index property_source_parcel_idx on property (source_parcel_id);

create table property_snapshot (
    property_snapshot_id uuid primary key default gen_random_uuid(),
    property_id uuid not null references property(property_id),
    source_record_id uuid not null references source_record(source_record_id),
    as_of_date date not null,
    owner_name_raw text,
    owner_name_normalized text,
    year_built integer,
    effective_year_built integer,
    stories numeric,
    assessor_sqft bigint,
    land_value numeric,
    improvement_value numeric,
    total_value numeric,
    assessed_value numeric,
    property_type text,
    legal_description text,
    subdivision text,
    lot text,
    block text,
    attributes jsonb not null default '{}'::jsonb,
    ingested_at timestamptz not null default now(),
    unique (property_id, source_record_id)
);

create index property_snapshot_property_date_idx
    on property_snapshot (property_id, as_of_date desc);

create table building (
    building_id uuid primary key default gen_random_uuid(),
    jurisdiction_id text not null,
    source_building_id text not null,
    property_id uuid references property(property_id),
    -- footprint polygons live in PMTiles; nullable here (see header)
    geometry geometry(MultiPolygon, 4326),
    centroid geometry(Point, 4326),
    footprint_sqft numeric,
    is_primary_building boolean,
    year_built integer,
    building_category text,
    stories numeric,
    assessor_sqft bigint,
    improvement_value numeric,
    match_method text,
    match_confidence numeric check (match_confidence between 0 and 1),
    source_record_id uuid references source_record(source_record_id),
    first_seen_at timestamptz not null default now(),
    last_seen_at timestamptz not null default now(),
    unique (jurisdiction_id, source_building_id)
);

create index building_centroid_gix on building using gist (centroid);
create index building_property_idx on building (property_id);

create table event (
    event_id uuid primary key default gen_random_uuid(),
    jurisdiction_id text not null,
    event_type text not null,
    event_subtype text,
    event_at timestamptz not null,
    observed_at timestamptz not null,
    title text not null,
    summary text,
    status text,
    amount numeric,
    source_record_id uuid not null references source_record(source_record_id),
    source_event_key text,
    geometry geometry(Geometry, 4326),
    sensitivity_class text not null default 'public_property_event',
    attributes jsonb not null default '{}'::jsonb,
    unique (source_record_id, event_type)
);

create index event_type_date_idx on event (event_type, event_at desc);
create index event_geometry_gix on event using gist (geometry);

create table event_property_match (
    event_id uuid not null references event(event_id) on delete cascade,
    property_id uuid not null references property(property_id) on delete cascade,
    match_method text not null,
    match_confidence numeric not null check (match_confidence between 0 and 1),
    is_primary boolean not null default true,
    evidence jsonb not null default '{}'::jsonb,
    primary key (event_id, property_id)
);

create index event_property_property_idx
    on event_property_match (property_id, event_id);

create table entity (
    entity_id uuid primary key default gen_random_uuid(),
    entity_type text not null check (entity_type in ('person', 'organization', 'trust', 'government', 'unknown')),
    display_name text not null,
    normalized_name text not null,
    canonical_status text not null default 'unreviewed',
    created_at timestamptz not null default now()
);

create index entity_name_trgm on entity using gin (normalized_name gin_trgm_ops);
create unique index entity_normalized_idx on entity (normalized_name);

create table entity_alias (
    entity_alias_id uuid primary key default gen_random_uuid(),
    entity_id uuid not null references entity(entity_id) on delete cascade,
    alias_raw text not null,
    alias_normalized text not null,
    source_record_id uuid references source_record(source_record_id),
    confidence numeric not null check (confidence between 0 and 1)
);

create table property_interest (
    property_interest_id uuid primary key default gen_random_uuid(),
    property_id uuid not null references property(property_id),
    entity_id uuid not null references entity(entity_id),
    role text not null,
    valid_from date,
    valid_to date,
    source_record_id uuid not null references source_record(source_record_id),
    confidence numeric not null check (confidence between 0 and 1),
    is_inferred boolean not null default false,
    unique (property_id, entity_id, role, source_record_id)
);

create index property_interest_property_idx on property_interest (property_id, valid_to);
create index property_interest_entity_idx on property_interest (entity_id, valid_to);

create table event_party (
    event_id uuid not null references event(event_id) on delete cascade,
    entity_id uuid not null references entity(entity_id),
    role text not null,
    source_record_id uuid not null references source_record(source_record_id),
    confidence numeric not null check (confidence between 0 and 1),
    primary key (event_id, entity_id, role)
);

create table entity_relationship (
    entity_relationship_id uuid primary key default gen_random_uuid(),
    from_entity_id uuid not null references entity(entity_id),
    to_entity_id uuid not null references entity(entity_id),
    relationship_type text not null,
    valid_from date,
    valid_to date,
    confidence numeric not null check (confidence between 0 and 1),
    is_inferred boolean not null default true,
    evidence jsonb not null,
    created_at timestamptz not null default now()
);
