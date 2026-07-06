# Arkansas / Pulaski County GIS Building Footprint and Year-Built Data Sources

_Last checked: July 5, 2026_

## Purpose

This file collects the public data sources found for:

1. GIS building footprint data for Pulaski County and Arkansas statewide.
2. Public bulk data that may contain the year each structure was built.
3. Practical guidance on how to join year-built assessor data back to GIS parcel/building data.

The key finding is straightforward: **building footprint GIS layers usually do not contain year-built information**. Year-built is normally stored in **county assessor CAMA / real property improvement records**, not in the building footprint geometry layer.

---

# 1. Statewide Arkansas Building Footprint Source

## Arkansas GIS Office — Building Footprints Composite

**Best statewide building footprint source:**

```text
https://gis.arkansas.gov/product/building-footprints-composite/
```

## Description

The Arkansas GIS Office publishes a statewide **Building Footprints Composite** dataset. It combines building footprint data from Microsoft and FEMA, clipped to Arkansas, with additional FEMA footprints appended where they were not present in the Microsoft dataset.

Official description notes:

- Dataset represents building footprints in Arkansas.
- Footprints were extracted from ortho imagery and published by Microsoft and FEMA.
- Arkansas GIS Office combined the two sources into a more comprehensive statewide layer.
- The data includes a field capturing the date range of imagery used to extract the footprint.
- The published dataset was projected to **NAD 83 UTM Zone 15N**.

## Important metadata

| Item | Value |
|---|---|
| Dataset name | `BLDG_FOOTPRINTS_COMPOSITE` |
| Publisher | FEMA and Microsoft; analysis by Arkansas GIS Office |
| Publication date | December 7, 2022 |
| Geometry | Polygon |
| Projection | NAD 83 UTM Zone 15N / EPSG:26915 |
| Category | Structure |

## REST endpoint

```text
https://gis.arkansas.gov/arcgis/rest/services/FEATURESERVICES/Structure/FeatureServer
```

## Building footprint layer

```text
https://gis.arkansas.gov/arcgis/rest/services/FEATURESERVICES/Structure/FeatureServer/54
```

## GeoJSON query example

```text
https://gis.arkansas.gov/arcgis/rest/services/FEATURESERVICES/Structure/FeatureServer/54/query?where=1%3D1&outFields=*&returnGeometry=true&f=geojson&outSR=4326
```

## Paged GeoJSON query example

The service has a small maximum record count, so use pagination.

```text
https://gis.arkansas.gov/arcgis/rest/services/FEATURESERVICES/Structure/FeatureServer/54/query?where=1%3D1&outFields=*&returnGeometry=true&f=geojson&outSR=4326&resultOffset=0&resultRecordCount=500
```

Then increment `resultOffset`:

```text
resultOffset=500
resultOffset=1000
resultOffset=1500
```

## Known fields

Expected fields include:

```text
objectid
release
capture_dates_range
source
globalid
Shape__Area
Shape__Length
```

## Does this contain year built?

**No obvious year-built field was found.**

This layer is useful for building footprint geometry, but not for construction year.

---

# 2. Pulaski County Building Footprint Source

## PAgis — Pulaski Area Geographic Information System

**Best Pulaski County local building footprint source:**

```text
https://www.pagis.org/arcgis/rest/services/MAPS/BaseMap/MapServer/21
```

## Description

PAgis maintains GIS layers for Pulaski County and the surrounding local jurisdictions. Its BaseMap service includes a **Building** polygon layer.

## REST root

```text
https://www.pagis.org/arcgis/rest/services
```

## BaseMap service

```text
https://www.pagis.org/arcgis/rest/services/MAPS/BaseMap/MapServer
```

## Building layer

```text
https://www.pagis.org/arcgis/rest/services/MAPS/BaseMap/MapServer/21
```

## GeoJSON query example

```text
https://www.pagis.org/arcgis/rest/services/MAPS/BaseMap/MapServer/21/query?where=1%3D1&outFields=*&returnGeometry=true&f=geojson&outSR=4326
```

## Paged GeoJSON query example

```text
https://www.pagis.org/arcgis/rest/services/MAPS/BaseMap/MapServer/21/query?where=1%3D1&outFields=*&returnGeometry=true&f=geojson&outSR=4326&resultOffset=0&resultRecordCount=1000
```

## Important metadata

| Item | Value |
|---|---|
| Layer name | Building |
| Layer ID | 21 |
| Geometry | Polygon |
| Projection | Arkansas State Plane South / WKID 3433 / 102651 |
| Max record count | 1000 |
| Supported query formats | JSON, GeoJSON, PBF |
| Supports pagination | Yes |

## Known fields

Fields found in the layer include:

```text
OBJECTID
BO_UNIQ
BO_NAME
STR_CODE
BO_CODE
BO_STAT_CO
SASC
Shape
VERSION
VER_DATE
VER_AGENCY
GlobalID
Shape_Length
Shape_Area
Override_2
created_user
created_date
last_edited_user
last_edited_date
```

## Does this contain year built?

**No obvious year-built field was found.**

This is likely the better local geometry source for Pulaski County buildings, but year-built still needs to come from assessor/CAMA data.

---

# 3. Pulaski County Year-Built Source

## Pulaski County Assessor — Raw Data Export

**Best public bulk source for year-built / improvement data in Pulaski County:**

```text
https://pulaskicountyassessor.net/services/raw-data-export/
```

The Pulaski County Assessor publishes raw data export files. The relevant file is the **Real Property** export.

## Real Property download

The Assessor page links to a Dropbox file named:

```text
CamaExport.zip
```

Dropbox URL shown from the Assessor source:

```text
https://www.dropbox.com/scl/fi/iogswewv3za77ocqcznj4/CamaExport.zip?dl=0&rlkey=8yh1qcm4ckw8y3t5oe5mlxdu3&st=byptnjxq
```

Direct-download version usually works by changing `dl=0` to `dl=1`:

```text
https://www.dropbox.com/scl/fi/iogswewv3za77ocqcznj4/CamaExport.zip?dl=1&rlkey=8yh1qcm4ckw8y3t5oe5mlxdu3&st=byptnjxq
```

## Why this matters

The Pulaski County Assessor raw export is the most likely public bulk source to contain:

- Parcel identifiers
- Real property records
- Improvement records
- Structure attributes
- Building year / year built fields

The exact field name must be verified after downloading and inspecting `CamaExport.zip`. Common CAMA-style field names may look like:

```text
YR_BUILT
YEAR_BUILT
BUILT_YEAR
ACTUAL_YEAR_BUILT
EFF_YEAR_BUILT
IMPR_YEAR
```

Do **not** assume the field name until the file contents are inspected.

## Assessor disclaimer summary

The Assessor states the raw data is provided for ease of obtaining records only and may contain delays, omissions, inaccuracies, or daily changes. Treat it as useful bulk data, not a legally authoritative final condition report.

---

# 4. Arkansas Statewide Parcel / CAMA-Linked GIS Source

## Arkansas GIS Office — Parcel Polygon CAMP

Statewide parcel geometry source:

```text
https://gis.arkansas.gov/arcgis/rest/services/FEATURESERVICES/Planning_Cadastre/FeatureServer/6
```

JSON metadata endpoint:

```text
https://gis.arkansas.gov/arcgis/rest/services/FEATURESERVICES/Planning_Cadastre/FeatureServer/6?f=pjson
```

## Description

This dataset contains polygon features representing approximate tax parcel locations from county assessor tax rolls. The Arkansas GIS Office integrates individual county data into a statewide publication.

The official metadata says county CAMA systems are used to populate database attributes for parcel polygons. It also warns that the statewide dataset is a publication snapshot and may lag behind production county data.

## Important fields to look for

Expected useful fields include:

```text
parcelid
camakey
camadate
pubdate
ownername
impvalue
landvalue
totalvalue
```

## Does this contain year built?

**No obvious year-built field was found in the published statewide parcel layer metadata.**

This layer is useful for parcel geometry and CAMA identifiers, but it does not appear to expose structure-level year-built attributes statewide.

---

# 5. Recommended Data Strategy

## If the goal is Pulaski County only

Use this approach:

1. Download **Pulaski County Assessor Real Property / CamaExport.zip**.
2. Extract and inspect the tables/files inside.
3. Identify the year-built field in the improvement/structure table.
4. Identify the parcel/CAMA/property key.
5. Join the assessor record to parcel geometry.
6. Optionally spatially join parcel records to building footprints.

Recommended geometry source order:

| Priority | Source | Use |
|---|---|---|
| 1 | PAgis Building layer | Local Pulaski County building footprints |
| 2 | PAgis parcel layers, if available | Local parcel geometry |
| 3 | Arkansas GIS Office Parcel Polygon CAMP | Statewide parcel geometry / fallback |
| 4 | Arkansas Building Footprints Composite | Statewide building footprints / fallback |

## If the goal is statewide Arkansas

Use this approach:

1. Use the Arkansas GIS Office **Building Footprints Composite** for statewide structure footprints.
2. Use Arkansas GIS Office **Parcel Polygon CAMP** for statewide parcel geometry and CAMA identifiers.
3. For year-built, collect assessor/CAMA exports county-by-county where available.
4. Join each county’s assessor data to parcels using county-specific parcel/CAMA keys.
5. Spatially associate building footprints to parcels.

There does not appear to be a single public statewide layer that exposes **year built for every structure**.

---

# 6. Practical Join Model

## Ideal join

```text
Assessor Improvement Table
        ↓ parcel_id / cama_key / property_id
Parcel Polygon Layer
        ↓ spatial relationship
Building Footprint Layer
```

## Example output target

A useful final dataset could look like this:

| Field | Source |
|---|---|
| building_geometry | PAgis Building or Arkansas Building Footprints Composite |
| parcel_geometry | PAgis parcel or Arkansas Parcel Polygon CAMP |
| parcel_id | Assessor / parcel GIS |
| cama_key | Assessor / parcel GIS |
| situs_address | Assessor / parcel GIS |
| owner_name | Assessor / parcel GIS, if needed |
| year_built | Assessor CAMA improvement table |
| effective_year_built | Assessor CAMA improvement table, if available |
| improvement_type | Assessor CAMA improvement table |
| source | Derived field |
| data_date | CAMADate / Assessor export date |

---

# 7. Suggested Workflow to Inspect CamaExport.zip

After downloading `CamaExport.zip`, inspect the file names first.

Possible contents may include CSV, DBF, TXT, MDB, or fixed-width files.

## Search for year-built fields

Look for fields containing:

```text
year
built
yr
impr
improvement
structure
residence
```

## Search for join fields

Look for fields containing:

```text
parcel
pid
property
prop
cama
key
account
real
```

## Validate before joining

Before trusting a join:

1. Pick 5 to 10 known addresses.
2. Confirm the parcel ID from the assessor site.
3. Confirm the same parcel ID exists in the GIS parcel layer.
4. Confirm the year-built field matches the assessor web lookup.
5. Only then automate the full county join.

---

# 8. Source URLs Summary

## Arkansas statewide building footprints

```text
https://gis.arkansas.gov/product/building-footprints-composite/
```

```text
https://gis.arkansas.gov/arcgis/rest/services/FEATURESERVICES/Structure/FeatureServer/54
```

## Pulaski County building footprints

```text
https://www.pagis.org/arcgis/rest/services/MAPS/BaseMap/MapServer/21
```

## Pulaski County Assessor raw real property export

```text
https://pulaskicountyassessor.net/services/raw-data-export/
```

```text
https://www.dropbox.com/scl/fi/iogswewv3za77ocqcznj4/CamaExport.zip?dl=1&rlkey=8yh1qcm4ckw8y3t5oe5mlxdu3&st=byptnjxq
```

## Arkansas statewide parcel polygons / CAMA-linked parcel publication

```text
https://gis.arkansas.gov/arcgis/rest/services/FEATURESERVICES/Planning_Cadastre/FeatureServer/6
```

---

# 9. Bottom Line

For **building footprints**, use:

```text
PAgis Building layer for Pulaski County
Arkansas GIS Office Building Footprints Composite for statewide Arkansas
```

For **year built**, use:

```text
Pulaski County Assessor Raw Data Export → Real Property → CamaExport.zip
```

For **joining year built to GIS**, use:

```text
Assessor CAMA/improvement records → parcel/CAMA key → parcel polygon → building footprint spatial join
```

No public statewide Arkansas structure footprint layer was found that directly includes year-built for every structure.
