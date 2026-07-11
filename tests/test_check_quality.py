"""Quality-gate rules (roadmap QA-002): each check fires on the failure it
guards and stays quiet on a healthy build."""
from check_quality import run_checks

GOOD_QUALITY = {
    "parcel_to_cama_match_rate": 0.97,
    "building_to_parcel_match_rate": 0.98,
    "building_year_rate": 0.90,
    "building_count": 225774,
}
GOOD_CFG = {
    "count": 225774,
    "bounds": [-92.87787, 34.4808, -92.02423, 35.02669],
    "cama_date": "2026-06-28",
}
PREV_CFG = {"count": 225774}


def test_healthy_build_passes():
    assert run_checks(GOOD_QUALITY, GOOD_CFG, PREV_CFG) == []


def test_collapsed_building_count_fails():
    cfg = dict(GOOD_CFG, count=90_000)
    fails = run_checks(GOOD_QUALITY, cfg, PREV_CFG)
    assert any("outside sane range" in f for f in fails)


def test_count_regression_vs_published_fails():
    cfg = dict(GOOD_CFG, count=190_000)   # in sane range, -16% vs published
    fails = run_checks(GOOD_QUALITY, cfg, PREV_CFG)
    assert any("tolerance" in f for f in fails)


def test_no_previous_config_skips_change_check():
    cfg = dict(GOOD_CFG, count=190_000)
    assert run_checks(GOOD_QUALITY, cfg, {}) == []


def test_match_rate_regression_fails():
    q = dict(GOOD_QUALITY, parcel_to_cama_match_rate=0.60)
    fails = run_checks(q, GOOD_CFG, PREV_CFG)
    assert any("parcel_to_cama_match_rate" in f for f in fails)


def test_missing_metric_fails():
    q = {k: v for k, v in GOOD_QUALITY.items() if k != "building_year_rate"}
    fails = run_checks(q, GOOD_CFG, PREV_CFG)
    assert any("missing: building_year_rate" in f for f in fails)


def test_escaped_bounds_fail():
    cfg = dict(GOOD_CFG, bounds=[-98.0, 30.0, -91.0, 36.0])
    fails = run_checks(GOOD_QUALITY, cfg, PREV_CFG)
    assert any("envelope" in f for f in fails)


def test_missing_cama_date_fails():
    cfg = dict(GOOD_CFG, cama_date="")
    fails = run_checks(GOOD_QUALITY, cfg, PREV_CFG)
    assert any("cama_date" in f for f in fails)


def test_future_cama_date_fails():
    cfg = dict(GOOD_CFG, cama_date="2099-01-01")
    fails = run_checks(GOOD_QUALITY, cfg, PREV_CFG)
    assert any("future" in f for f in fails)
