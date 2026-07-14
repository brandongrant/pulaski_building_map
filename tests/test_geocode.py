"""Geocoding must VERIFY an address before placing a precise point.

Regression guard for the bug where "16105 CHENAL PKY" (feed spelling) missed
the index ("...PKWY") and fell back to the street centroid — dropping every
un-matched call on a street onto one wrong averaged point, which read as a
phantom hotspot. The fix: fold street-type/direction synonyms, interpolate by
house number, and return `failed` (no point) rather than a centroid guess.
"""
import gzip
import json

import pytest

from addr_norm import canon_addr, street_variants
from dispatch_collect import Geocoder, categorize


def test_suffix_synonyms_fold():
    assert canon_addr("16105 CHENAL PKY") == "16105 CHENAL PKWY"
    assert canon_addr("16105 CHENAL PARKWAY") == "16105 CHENAL PKWY"
    assert canon_addr("600 S UNIVERSITY AVENUE") == "600 S UNIVERSITY AVE"
    assert canon_addr("123 west markham street") == "123 W MARKHAM ST"


def test_intersection_canon():
    assert canon_addr("MAIN ST / BROADWAY AVE") == "MAIN ST / BROADWAY AVE"
    assert " / " in canon_addr("7TH / MAIN")


def test_street_variants_reach_canonical_type():
    v = street_variants("", "CHENAL", "PKY", "")
    assert "CHENAL PKWY" in v and "CHENAL" in v


@pytest.fixture
def geo(tmp_path):
    # a street "MAIN ST" with house-number points at 100 and 300; the canonical
    # exact key for 100 MAIN ST is present, 300 is present, 200 is NOT
    idx = {
        "addr": {"100 MAIN ST": [-92.30, 34.70], "300 MAIN ST": [-92.30, 34.72]},
        "streets": {"MAIN ST": [[-92.30, 34.70, 100], [-92.30, 34.72, 300]],
                    "MAIN": [[-92.30, 34.70, 100], [-92.30, 34.72, 300]]},
    }
    p = tmp_path / "idx.json.gz"
    with gzip.open(p, "wt", encoding="utf-8") as f:
        json.dump(idx, f)
    return Geocoder(p)


def test_exact_match(geo):
    lon, lat, q = geo.geocode("100 MAIN ST")
    assert q == "exact_address" and (lon, lat) == (-92.30, 34.70)


def test_suffix_mismatch_still_hits_exact(geo):
    # "MAIN STREET" folds to "MAIN ST" -> exact, not a centroid guess
    assert geo.geocode("300 MAIN STREET")[2] == "exact_address"


def test_house_number_interpolation(geo):
    lon, lat, q = geo.geocode("200 MAIN ST")
    assert q == "interpolated"
    assert lat == pytest.approx(34.71, abs=1e-6)      # halfway between 100 and 300


def test_unknown_address_is_not_guessed(geo):
    # far outside the known house-number range -> no point (was a centroid before)
    assert geo.geocode("9000 MAIN ST") == (None, None, "failed")


def test_street_only_is_not_guessed(geo):
    # a bare street with no house number must not resolve to the centroid
    assert geo.geocode("MAIN ST")[2] == "failed"


def test_categorize_covers_new_buckets():
    assert categorize("SHOTS FIRED") == "shots"
    assert categorize("BURGLAR ALARM") == "alarm"          # alarm beats burglary
    assert categorize("BURGLARY IN PROGRESS") == "burglary"
    assert categorize("ASSIST MEDICAL") == "welfare"
    assert categorize("DOMESTIC DISTURBANCE") == "domestic"  # domestic beats disturbance
    assert categorize("SOME UNLISTED CALL") == "other"
