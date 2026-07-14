"""`SENSITIVE_RE` classifies medical/welfare/mental-health/death/sex/domestic/
juvenile call types.

Until 2026-07-13 this flag suppressed those calls from the precise point layers.
The site owner then chose to map every call type (see jurisdictions/ar/
pulaski.yml display_policy), so the flag is now informational metadata (it drives
the "sensitive call type" note in popups) rather than a display filter. These
tests still pin the classification so the labeling stays correct."""
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
