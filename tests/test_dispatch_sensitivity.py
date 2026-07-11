"""The dispatch sensitive-category filter is a privacy guarantee: these call
types must never appear as exact public points (see jurisdictions/ar/pulaski.yml
display_policy and docs/IMPLEMENTATION_ROADMAP.md §16.2)."""
import pytest

from dispatch_collect import SENSITIVE_RE

MUST_BE_SENSITIVE = [
    "MEDICAL EMERGENCY",
    "DEATH INVESTIGATION",
    "SUBJECT DOWN",
    "WELFARE CHECK",
    "SUICIDAL PERSON",
    "OVERDOSE",
    "MENTAL HEALTH CRISIS",
    "JUVENILE PROBLEM",
    "RAPE REPORT",
    "SEX OFFENSE",
    "DOMESTIC DISTURBANCE",
]

MUST_BE_PUBLIC = [
    "BURGLARY RESIDENTIAL",
    "THEFT OF PROPERTY",
    "TRAFFIC ACCIDENT",
    "ALARM COMMERCIAL",
    "SHOTS FIRED",
    "VANDALISM",
]


@pytest.mark.parametrize("call_type", MUST_BE_SENSITIVE)
def test_sensitive_categories_are_flagged(call_type):
    assert SENSITIVE_RE.search(call_type), f"{call_type} must be sensitive"


@pytest.mark.parametrize("call_type", MUST_BE_PUBLIC)
def test_ordinary_categories_are_not_flagged(call_type):
    assert not SENSITIVE_RE.search(call_type), f"{call_type} wrongly sensitive"
