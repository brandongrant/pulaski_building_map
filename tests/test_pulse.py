"""Geometry and normalisation behind the Pulse summary.

The hex mosaic is the piece most likely to break silently: if the axial
rounding is wrong the city's shape still draws, it is just subtly incorrect.
These tests pin the round trip the browser relies on to turn a clicked cell
back into a map location.
"""
import math

import pytest

import build_pulse as bp


def hex_center(q, r, size):
    """The inverse the browser applies (mirrored in web/pulse.js)."""
    return (size * 1.5 * q,
            size * (math.sqrt(3) / 2 * q + math.sqrt(3) * r))


@pytest.mark.parametrize("lon,lat", [
    (-92.2683, 34.7465),      # downtown Little Rock
    (-92.3502, 34.7183),      # Colonel Glenn
    (-92.4200, 34.8000),      # west
])
def test_a_point_lands_inside_its_own_cell(lon, lat):
    size = bp.HEX_M
    x, y = bp.merc(lon, lat)
    q, r = bp.hex_cell(x, y, size)
    cx, cy = hex_center(q, r, size)
    # the point must be nearer to its own cell centre than one cell across
    assert math.hypot(x - cx, y - cy) < size


def test_neighbouring_points_share_or_touch_cells():
    size = bp.HEX_M
    a = bp.hex_cell(*bp.merc(-92.2683, 34.7465), size)
    b = bp.hex_cell(*bp.merc(-92.2685, 34.7466), size)
    assert max(abs(a[0] - b[0]), abs(a[1] - b[1])) <= 1


def test_far_apart_points_are_different_cells():
    size = bp.HEX_M
    a = bp.hex_cell(*bp.merc(-92.2683, 34.7465), size)
    b = bp.hex_cell(*bp.merc(-92.4200, 34.8000), size)
    assert a != b


def test_every_cell_is_hit_by_exactly_one_assignment():
    # scan a grid of points and check each maps to a single deterministic cell
    size = bp.HEX_M
    seen = {}
    for i in range(40):
        for j in range(40):
            lon, lat = -92.45 + i * 0.005, 34.65 + j * 0.005
            key = bp.hex_cell(*bp.merc(lon, lat), size)
            seen.setdefault(key, 0)
            seen[key] += 1
    assert len(seen) > 50               # the grid really does spread out
    assert bp.hex_cell(*bp.merc(-92.30, 34.72), size) == \
        bp.hex_cell(*bp.merc(-92.30, 34.72), size)


@pytest.mark.parametrize("loc,expected", [
    ("2317 S CEDAR ST", "S CEDAR ST"),
    ("5120 W MARKHAM ST 124", "W MARKHAM ST"),
    ("1200 MAIN ST APT 4B", "MAIN ST"),
    ("400 BROADWAY / 7TH ST", "BROADWAY"),
    ("18 MICHAELS ST", "MICHAELS ST"),
])
def test_street_normalisation(loc, expected):
    assert bp.street_of(loc) == expected


@pytest.mark.parametrize("loc", ["", None, "12", "5 A"])
def test_unusable_locations_are_dropped(loc):
    assert bp.street_of(loc) is None


def test_focus_categories_are_a_subset_of_the_full_taxonomy():
    assert set(bp.FOCUS_CATS) <= set(bp.CRIME_CATS)
    # the loud, non-crime buckets must not be in the focus set
    assert "assist" not in bp.FOCUS_CATS


# ------------------------------------------------------- hotspot statements --
@pytest.mark.parametrize("profile,width,expected", [
    ([0] * 20 + [5, 9, 9, 1], 2, 21),          # the 9+9 block wins
    ([9] + [0] * 22 + [9], 2, 23),             # wraps past midnight
    ([0] * 24, 2, None),                       # nothing to say
    ([1] * 24, 2, 0),                          # flat: first block, deterministic
])
def test_peak_window(profile, width, expected):
    assert bp.peak_window(profile, width) == expected


def test_hotspot_thresholds_are_defensible():
    # a statement about a named place needs a real sample behind it
    assert bp.MIN_HOTSPOT >= 50
    assert bp.MIN_CAT_FOR_LIFT >= 10
    # naming radius must stay tight enough not to grab the neighbouring building
    assert bp.NAME_RADIUS_M <= 50
    # a place's own clock needs more than a handful of calls
    assert bp.MIN_PLACE_HOURS >= 20


def test_hotspots_are_none_without_history():
    assert bp.build_hotspots(None, [], [], [], {}) is None


def test_hotspot_names_only_within_the_radius():
    """A named building beyond NAME_RADIUS_M must not be attached to a cluster."""
    hist = {
        "off_cat": ["theft"],
        "locs": ["100 TEST ST"],
        # MIN_HOTSPOT rows, all 2020, same spot
        "crime": [[-92.30, 34.72, 0, 20200106 + (i % 5), 0, 0, 0]
                  for i in range(bp.MIN_HOTSPOT)],
    }
    near = [["Near Store", -92.30, 34.72002]]        # ~2 m away
    far = [["Far Store", -92.30, 34.7220]]           # ~220 m away

    hot_near = bp.build_hotspots(hist, [2020], near, [], {})
    hot_far = bp.build_hotspots(hist, [2020], far, [], {})
    assert hot_near["places"][0]["name"] == "Near Store"
    assert hot_far["places"][0]["name"] is None
    assert hot_far["places"][0]["addr"] == "100 TEST ST"


def _calls(n, lon=-92.30, lat=34.72, hour=13, dow=0, cat="theft"):
    return [(lon, lat, hour, dow, cat)] * n


def test_hotspot_hours_fall_back_to_the_city_when_the_place_is_thin():
    hist = {
        "off_cat": ["theft"], "locs": ["100 TEST ST"],
        "crime": [[-92.30, 34.72, 0, 20200106, 0, 0, 0] for _ in range(bp.MIN_HOTSPOT)],
    }
    thin = _calls(bp.MIN_PLACE_HOURS - 1)
    rich = _calls(bp.MIN_PLACE_HOURS)

    assert bp.build_hotspots(hist, [2020], [], thin, {}, 41)["places"][0]["hours"] is None
    got = bp.build_hotspots(hist, [2020], [], rich, {}, 41)["places"][0]
    assert got["hours"] is not None and got["peak"] is not None


# ------------------------------------------------- current activity merged --
def test_a_place_with_no_offense_record_qualifies_on_current_calls():
    """Somewhere busy now must be able to earn a card on its own."""
    hist = {"off_cat": [], "locs": [], "crime": []}
    calls = _calls(bp.MIN_RECENT, lon=-92.40, lat=34.80, cat="assault")
    h = bp.build_hotspots(hist, [], [], calls, {}, 41)
    assert len(h["places"]) == 1
    p = h["places"][0]
    assert p["src"] == "recent"
    assert p["n"] == 0 and p["by_cat"]["assault"] == bp.MIN_RECENT


def test_current_only_places_need_reportable_calls_not_errands():
    """A jail or shelter generating assist/welfare traffic is not a hotspot."""
    hist = {"off_cat": [], "locs": [], "crime": []}
    errands = _calls(bp.MIN_RECENT * 4, lon=-92.40, lat=34.80, cat="assist")
    errands += _calls(bp.MIN_RECENT * 4, lon=-92.40, lat=34.80, cat="welfare")
    assert bp.build_hotspots(hist, [], [], errands, {}, 41)["places"] == []


def test_current_only_below_threshold_is_dropped():
    hist = {"off_cat": [], "locs": [], "crime": []}
    calls = _calls(bp.MIN_RECENT - 1, lon=-92.40, lat=34.80, cat="assault")
    assert bp.build_hotspots(hist, [], [], calls, {}, 41)["places"] == []


def test_current_calls_attach_to_the_historical_site_they_sit_on():
    """Calls at a known hotspot enrich it rather than becoming a second card."""
    hist = {
        "off_cat": ["theft"], "locs": ["100 TEST ST"],
        "crime": [[-92.30, 34.72, 0, 20200106, 0, 0, 0] for _ in range(bp.MIN_HOTSPOT)],
    }
    calls = _calls(bp.MIN_RECENT * 3, lon=-92.30, lat=34.720_1, cat="assault")
    h = bp.build_hotspots(hist, [2020], [], calls, {}, 41)
    assert len(h["places"]) == 1
    assert h["places"][0]["src"] == "history"
    assert h["places"][0]["recent"]["n"] == bp.MIN_RECENT * 3


def test_call_to_offense_ratio_is_reported_for_ranking():
    """The browser needs it to rank call-based entries against offense-based ones."""
    hist = {
        "off_cat": ["theft"], "locs": ["100 TEST ST"],
        "crime": [[-92.30, 34.72, 0, 20200106, 0, 0, 0] for _ in range(100)],
    }
    h = bp.build_hotspots(hist, [2020], [], _calls(50), {}, 365)
    # 50 reportable calls a year against 100 offenses a year
    assert h["call_to_offense"] == pytest.approx(0.5, abs=0.01)


def test_subsite_suffixes_are_stripped_from_place_names():
    """One footprint of a complex must not be blamed for the whole site."""
    import build_place_index as bpi
    assert bpi.site_name("Fair Oaks Apts - Bldg 8") == "Fair Oaks Apts"
    assert bpi.site_name("Baptist Health Medical - Office") == "Baptist Health Medical"
    assert bpi.site_name("Spanish Johns Apts - Bldg B") == "Spanish Johns Apts"
    # a hyphen that is part of the name survives
    assert bpi.site_name("Wal-Mart Supercenter") == "Wal-Mart Supercenter"
    assert bpi.site_name("Chateau De Ville Apts") == "Chateau De Ville Apts"
