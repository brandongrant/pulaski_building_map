"""The surveillance trail is only worth anything if the facts are literal.

Two things are guarded here. First, extraction: a dollar figure, a resolution
number or an account code that the app displays must be a string that actually
appears in the document, including the awkward cases (a contract number broken
across a line, "Resolutions 15,844 and 16,202" with no "No.", a clerk typing
15.892 for 15,892). Second, the merge: the published sources and the
volunteer-mapped ones describe some of the same physical poles, and counting
one camera twice would overstate the map.
"""
import json
from pathlib import Path

import pytest

import build_surveillance as bs
import surveillance_docs as sd

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "pipeline" / "surveillance"
WEB = REPO_ROOT / "web" / "data" / "surveillance"


# --------------------------------------------------------------- extraction
def test_money_skips_line_numbers_and_sorts_by_size():
    text = "1 Section 1. $77,500.00 plus fees. 2 See page $12. Total $690,000.00"
    values = [a["value"] for a in sd.money_values(text)]
    assert values == [690000.0, 77500.0]          # $12 dropped as noise
    assert sd.money_values(text)[0]["literal"] == "$690,000.00"


def test_resolution_numbers_without_the_word_no():
    facts, _ = sd.extract(
        "approved with Resolution 15,392 and amended with Resolutions 15,844 "
        "and 16,202.", "")
    assert facts["resolutions"] == ["15,392", "15,844", "16,202"]


def test_resolution_typo_normalised():
    """The Feb 2025 resolution prints the same number as 15,892 and 15.892."""
    facts, _ = sd.extract("Resolution No. 15.892 (February 7, 2023)", "")
    assert facts["resolutions"] == ["15,892"]


def test_cooperative_contract_survives_a_line_break():
    facts, _ = sd.extract(
        "Vendor selection was made through the OMINA Partners #23-6692-\n03.", "")
    assert facts["cooperative_contracts"] == ["Omnia Partners 23-6692-03"]


def test_account_number_extracted():
    facts, _ = sd.extract("This will be paid from Account No. 105225-63360.", "")
    assert facts["accounts"] == ["105225-63360"]


def test_agenda_date_read_from_the_url():
    assert sd.guess_date("", ".../AGENDA%20-%20WEB%20-%2010-21-2025/BC.pdf") == "2025-10-21"


def test_kind_and_body_detected():
    memo = "OFFICE OF THE CITY MANAGER\nLITTLE ROCK, ARKANSAS\nBOARD OF DIRECTORS COMMUNICATION"
    assert sd.guess_kind(memo) == "board_communication"
    assert sd.guess_body(memo, "") == "Little Rock Board of Directors"


def test_title_prefers_the_resolution_clause_over_layout_noise():
    text = ("1 RESOLUTION NO. _______\n2\n3 A RESOLUTION TO AUTHORIZE THE CITY "
            "MANAGER TO ENTER\n4 INTO A CONTRACT; AND FOR OTHER PURPOSES.")
    assert sd.guess_title(text, "").lower().startswith(
        "a resolution to authorize the city manager")


# ------------------------------------------------------------------ geometry
def test_plus_code_decodes_into_little_rock():
    lon, lat, _ = bs.plus_code("PMW8+66, Little Rock, AR undefined, US")
    assert 34.6 < lat < 34.9 and -92.6 < lon < -92.1


def test_plus_code_round_trips():
    lat, lon = 34.7465, -92.2896
    back_lat, back_lon = bs.olc_decode(bs.olc_encode(lat, lon))
    assert abs(back_lat - lat) < 0.001 and abs(back_lon - lon) < 0.001


def test_plain_address_is_not_mistaken_for_a_plus_code():
    assert bs.plus_code("5944 Rebsamen Park Rd, Little Rock, AR 72207") is None


def test_haversine_matches_a_known_distance():
    # I-430 river bridge to the I-630 interchange, about 6.2 km apart.
    d = bs.haversine(34.8026, -92.3705, 34.7469, -92.3904)
    assert 5800 < d < 6800


# --------------------------------------------------------------------- merge
def _pin(lon, lat, fam="alpr", **props):
    props.setdefault("id", "x")
    props["fam"] = fam
    return {"type": "Feature", "properties": props,
            "geometry": {"type": "Point", "coordinates": [lon, lat]}}


def test_same_pole_is_not_counted_twice():
    foia = [_pin(-92.30, 34.75, id="foia-000")]
    osm = [_pin(-92.30001, 34.75001, id="osm-1", dir=90.0)]   # ~1 m away
    kept, merged = bs.merge_duplicate_poles(foia, osm)
    assert merged == 1 and kept == []
    assert foia[0]["properties"]["osm_seen"] == 1
    assert foia[0]["properties"]["dir"] == 90.0      # direction carried over


def test_a_different_pole_is_kept():
    foia = [_pin(-92.30, 34.75, id="foia-000")]
    osm = [_pin(-92.32, 34.77, id="osm-1")]          # ~2.7 km away
    kept, merged = bs.merge_duplicate_poles(foia, osm)
    assert merged == 0 and len(kept) == 1


def test_non_alpr_devices_are_never_merged_away():
    foia = [_pin(-92.30, 34.75, id="foia-000")]
    osm = [_pin(-92.30, 34.75, fam="gunshot", id="osm-9")]
    kept, _ = bs.merge_duplicate_poles(foia, osm)
    assert [f["properties"]["id"] for f in kept] == ["osm-9"]


def test_unattributed_flock_is_not_claimed_for_any_agency():
    assert bs.alpr_program({"manufacturer": "Flock Safety"}) == "flock-unattributed"
    assert bs.alpr_program(
        {"manufacturer": "Flock Safety",
         "operator": "Little Rock Police Department"}) == "flock-lrpd"
    assert bs.alpr_program(
        {"manufacturer": "Flock Safety", "operator": "The Home Depot"}) == "flock-other"
    assert bs.alpr_program({"manufacturer": "Genetec"}) == "other-alpr"


def test_spend_timeline_never_sums_overlapping_renewals():
    docs = [{"id": "a", "date": "2025-10-21", "programs": ["flock-lrpd"], "title": "t",
             "amounts": [{"value": 690000.0, "literal": "$690,000.00"},
                         {"value": 345000.0, "literal": "$345,000.00"}]}]
    rows = bs.spend_timeline(docs)
    assert len(rows) == 1 and rows[0]["amount"] == 690000.0


# ------------------------------------------------------------ built outputs
@pytest.fixture(scope="module")
def built():
    if not (WEB / "devices.geojson").exists():
        pytest.skip("run pipeline/build_surveillance.py first")
    return (json.loads((WEB / "devices.geojson").read_text(encoding="utf-8")),
            json.loads((WEB / "programs.json").read_text(encoding="utf-8")),
            json.loads((WEB / "documents.json").read_text(encoding="utf-8")))


def test_every_device_names_a_programme_that_exists(built):
    devices, programs, _ = built
    for f in devices["features"]:
        assert f["properties"]["prog"] in programs, f["properties"]["id"]
        assert f["properties"]["fam"]


def test_every_device_is_inside_the_stated_window(built):
    devices, _, _ = built
    south, west, north, east = bs.BBOX
    for f in devices["features"]:
        lon, lat = f["geometry"]["coordinates"]
        assert south <= lat <= north and west <= lon <= east


def test_documents_only_attach_to_real_programmes(built):
    _, programs, documents = built
    for d in documents:
        for pid in d.get("programs", []):
            assert pid in programs, f"{d['id']} -> {pid}"


def test_every_document_keeps_its_evidence(built):
    """The extracted text is committed next to the entry so a claim can be checked."""
    _, _, documents = built
    for d in documents:
        assert (SRC / "doc_text" / f"{d['id']}.txt").exists(), d["id"]
        assert d["url"].startswith("http")
        assert d["sha256"]


def test_sightings_state_a_confidence(built):
    devices, _, _ = built
    sightings = [f for f in devices["features"] if f["properties"]["fam"] == "sighting"]
    assert sightings
    for f in sightings:
        assert f["properties"]["conf"] in {"confirmed", "likely", "probable", "uncertain"}
        # every sighting is scored against the published camera list
        assert f["properties"].get("near_cam_m") is not None
