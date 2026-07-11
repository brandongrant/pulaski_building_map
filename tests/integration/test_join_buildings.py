"""End-to-end join_buildings.py run against a synthetic mini-county.

Golden micro-fixtures (roadmap QA-001 / ID-001):
  parcel P1 (matched to CAMA, two buildings — house + shed)
  parcel P2 (no CAMA row — parcel values only)
  building B4 outside every parcel (no match)

Asserts the parcel identity, values, and match provenance carried into
buildings_final.pkl, plus main-building selection per parcel.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

gpd = pytest.importorskip("geopandas")
pd = pytest.importorskip("pandas")

REPO_ROOT = Path(__file__).resolve().parents[2]


# Coordinates must sit inside UTM zone 15N (the pipeline computes footprint
# area in EPSG:26915) — build the mini-county just west of Little Rock.
LON0, LAT0 = -92.40, 34.70


def _square(x, y, size=1.0):
    """Unit-ish square: x/y/size are offsets in thousandths of a degree."""
    lon, lat, s = LON0 + x / 1000, LAT0 + y / 1000, size / 1000
    return {"type": "Polygon", "coordinates": [[
        [lon, lat], [lon + s, lat], [lon + s, lat + s], [lon, lat + s], [lon, lat]]]}


def _feature(geom, props):
    return {"type": "Feature", "geometry": geom, "properties": props}


@pytest.fixture(scope="module")
def pipeline_output(tmp_path_factory):
    data_root = tmp_path_factory.mktemp("data_root")
    raw = data_root / "raw"
    processed = data_root / "processed"
    raw.mkdir()
    processed.mkdir()

    parcels = {"type": "FeatureCollection", "features": [
        _feature(_square(0, 0, 10), {
            "OBJECTID": 1, "PARCELID": "34L-010.00-001.00", "CAMA_PIN": "1001",
            "ADRLABEL": "100 MAIN ST", "ADRCITY": "LITTLE ROCK",
            "PARCELTYPE": "R", "IMPVALUE": 150000, "LANDVALUE": 40000,
            "TOTALVALUE": 190000}),
        _feature(_square(20, 0, 10), {
            "OBJECTID": 2, "PARCELID": "34L-010.00-002.00", "CAMA_PIN": "1002",
            "ADRLABEL": "102 MAIN ST", "ADRCITY": "LITTLE ROCK",
            "PARCELTYPE": "C", "IMPVALUE": 500000, "LANDVALUE": 250000,
            "TOTALVALUE": 750000}),
    ]}
    (raw / "parcels.geojson").write_text(json.dumps(parcels))

    buildings = {"type": "FeatureCollection", "features": [
        _feature(_square(1, 1, 5), {"OBJECTID": 11, "BO_CODE": "S"}),   # house on P1
        _feature(_square(7, 7, 1), {"OBJECTID": 12, "BO_CODE": "S"}),   # shed on P1
        _feature(_square(22, 2, 6), {"OBJECTID": 13, "BO_CODE": "C"}),  # store on P2
        _feature(_square(50, 50, 3), {"OBJECTID": 14, "BO_CODE": "S"}), # no parcel
    ]}
    (raw / "buildings.geojson").write_text(json.dumps(buildings))

    cama = pd.DataFrame([
        # matches P1 after alphanumeric-upper normalization
        {"ParcelNumber": "34L0100000100", "year_built": 1962, "stories": 1.0,
         "sqft": 1800, "category": "sfr"},
        # no CAMA row for P2 — its buildings keep parcel values, no year
    ])
    cama.to_pickle(processed / "cama_parcel_attrs.pkl")

    env = dict(os.environ, PULASKI_DATA_ROOT=str(data_root))
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "pipeline" / "join_buildings.py")],
        env=env, cwd=REPO_ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    return pd.read_pickle(processed / "buildings_final.pkl").set_index("id")


def test_all_buildings_survive_the_join(pipeline_output):
    assert sorted(pipeline_output.index) == [11, 12, 13, 14]


def test_parcel_id_carried_through(pipeline_output):
    out = pipeline_output
    assert out.loc[11, "pid"] == "34L-010.00-001.00"
    assert out.loc[12, "pid"] == "34L-010.00-001.00"
    assert out.loc[13, "pid"] == "34L-010.00-002.00"
    assert out.loc[14, "pid"] == ""


def test_cama_pin_carried_through(pipeline_output):
    assert pipeline_output.loc[11, "cama_pin"] == "1001"
    assert pipeline_output.loc[14, "cama_pin"] == ""


def test_match_method_recorded(pipeline_output):
    out = pipeline_output
    assert set(out.loc[[11, 12, 13], "pmatch"]) == {"point_in_parcel"}
    assert out.loc[14, "pmatch"] == ""


def test_parcel_values_carried_through(pipeline_output):
    out = pipeline_output
    assert out.loc[11, "lval"] == 40000
    assert out.loc[11, "tval"] == 190000
    assert out.loc[13, "lval"] == 250000
    assert out.loc[13, "tval"] == 750000
    assert out.loc[14, "lval"] == 0


def test_cama_attributes_join_by_normalized_key(pipeline_output):
    out = pipeline_output
    assert out.loc[11, "yr"] == 1962          # CAMA matched P1
    assert out.loc[13, "yr"] == 0             # P2 has no CAMA row


def test_main_building_is_largest_per_parcel(pipeline_output):
    out = pipeline_output
    assert out.loc[11, "main"] == 1           # 5x5 house
    assert out.loc[12, "main"] == 0           # 1x1 shed on same parcel
    assert out.loc[13, "main"] == 1
    assert out.loc[14, "main"] == 1           # unmatched counts as main
