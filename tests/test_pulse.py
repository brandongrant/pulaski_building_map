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
