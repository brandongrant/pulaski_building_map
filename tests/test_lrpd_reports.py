"""Parsing and publishing rules for the LRPD daily incident reports.

The PDFs themselves are not committed as fixtures: they contain victim,
witness and suspect names, and the point of this pipeline is that none of that
is retained. These tests pin the pure functions and, most importantly, the
publish contract — the set of fields allowed to leave the pipeline.
"""
import re

import pytest

import lrpd_reports
import reports_collect


# --------------------------------------------------------------- privacy ----
def test_narrative_is_never_published():
    assert "narrative" not in reports_collect.PUBLISH_FIELDS


def test_published_fields_are_incident_level():
    # nothing person-shaped is allowed in the published record
    banned = {"name", "victim", "suspect", "arrestee", "dob", "race", "sex",
              "officer", "narrative", "party"}
    for field in reports_collect.PUBLISH_FIELDS:
        assert not any(b in field for b in banned), field


def test_tags_describe_events_not_people():
    # every tag key must exist in the label table, and no label may describe
    # a person's characteristics
    for key in dict(lrpd_reports.TAG_RULES):
        assert key in lrpd_reports.TAG_LABELS
    words = set(re.findall(r"[a-z]+", " ".join(lrpd_reports.TAG_LABELS.values()).lower()))
    assert words.isdisjoint(
        {"male", "female", "black", "white", "hispanic", "age", "aged", "old",
         "name", "named", "juvenile", "victim", "witness"})


# ------------------------------------------------------------------ tags ----
@pytest.mark.parametrize("text,expected", [
    ("SUSPECT DISCHARGED A FIREARM INTO THE BUSINESS", "firearm"),
    ("THE SUSPECT STABBED THE VICTIM WITH A KNIFE", "knife"),
    ("ENTRY WAS MADE AFTER THE SUSPECT BROKE A WINDOW", "forced_entry"),
    ("THE SUSPECT TOOK HIS WALLET AND FLED", "property_taken"),
    ("TRANSPORTED TO THE HOSPITAL FOR TREATMENT", "injury"),
    ("THE SUSPECT WAS TAKEN INTO CUSTODY", "arrest"),
    ("SURVEILLANCE FOOTAGE WAS COLLECTED", "camera"),
    ("SUSPECT FLED WEST BOUND ON FOOT", "fled"),
    ("AN UNKNOWN BLACK MALE APPROACHED", "suspect_unknown"),
])
def test_tag_is_detected(text, expected):
    assert expected in lrpd_reports.tags_for(text)


def test_tags_are_empty_for_empty_text():
    assert lrpd_reports.tags_for("") == []


# ------------------------------------------------------------- call types ----
def test_known_call_type_gets_a_readable_label():
    assert lrpd_reports.call_type_label("ROBBUS") == "Robbery — business"


def test_unknown_call_type_degrades_to_the_code():
    assert lrpd_reports.call_type_label("NEWCODE") == "Newcode"
    assert lrpd_reports.call_type_label("") == ""


# ------------------------------------------------------------------ dates ----
@pytest.mark.parametrize("date_s,time_s,expected", [
    ("8/13/2026 5:41:57 PM", "17:41:00", "2026-08-13T17:41"),
    ("8/13/2026 12:05:00 AM", "00:05:00", "2026-08-13T00:05"),
    ("8/13/2026 12:05:00 PM", "12:05:00", "2026-08-13T12:05"),
    ("08/09/2026", "16:36:00", "2026-08-09T16:36"),
    ("08/09/2026", "", "2026-08-09T00:00"),
])
def test_datetime_normalisation(date_s, time_s, expected):
    assert lrpd_reports._norm_dt(date_s, time_s) == expected


def test_unparseable_date_is_none():
    assert lrpd_reports._norm_dt("not a date", "") is None


# -------------------------------------------------------------- locations ----
def test_redaction_stamp_words_are_stripped_from_a_location():
    assert lrpd_reports._clean_loc("6818  COLONEL GLENN RD Redact Before Release") \
        == "6818 COLONEL GLENN RD"


@pytest.mark.parametrize("narr,expected", [
    ("OFFICERS RESPONDED TO 6221 COLONEL GLENN IN REFERENCE TO A ROBBERY",
     "6221 COLONEL GLENN"),
    ("OFFICERS WERE DISPATCHED TO 6201 MABELVALE CUTOFF FOR SHOTS FIRED",
     "6201 MABELVALE CUTOFF"),
])
def test_address_recovered_from_a_narrative(narr, expected):
    m = lrpd_reports.NARR_ADDR_RE.search(narr)
    assert m and m.group(1).strip() == expected


# ----------------------------------------------------------- categorising ----
@pytest.mark.parametrize("offenses,expected", [
    (["AGGRAVATED ROBBERY (BUSINESS)", "THEFT OF PROPERTY FELONY"], "robbery"),
    (["UNLAWFUL DISCHARGE OF A FIREARM", "TERRORISTIC ACT"], "shots"),
    (["BURGLARY RESIDENTIAL"], "burglary"),
    (["THEFT OF PROPERTY MISD"], "theft"),
    (["CRIMINAL MISCHIEF 1ST DEGREE FELONY"], "vandalism"),
    (["FRAUDULENT USE OF A CREDIT CARD OR DEBIT CARD"], "fraud"),
])
def test_offences_map_onto_the_dispatch_taxonomy(offenses, expected):
    assert reports_collect.classify({"offenses": offenses}) == expected


def test_scanned_report_falls_back_to_tags():
    # no cover page parsed: offences and call type are both missing
    assert reports_collect.classify({"tags": ["firearm", "fled"]}) == "shots"
    assert reports_collect.classify({"tags": ["property_taken"]}) == "theft"
    assert reports_collect.classify({}) == "other"


# --------------------------------------------------------------- scraping ----
def test_report_links_are_listed_newest_first():
    html = """
      <li><a href="https://littlerock.gov/wp-content/uploads/08-03-2026.pdf">08-03-2026</a></li>
      <li><a href="https://littlerock.gov/wp-content/uploads/08-14-2026.pdf">08-14-2026</a></li>
      <li><a href="https://littlerock.gov/wp-content/uploads/08-14-2026.pdf">dupe</a></li>
      <li><a href="https://littlerock.gov/wp-content/uploads/some-other-file.pdf">nope</a></li>
    """
    listed = reports_collect.list_reports(html)
    assert [d for d, _ in listed] == ["2026-08-14", "2026-08-03"]
    assert listed[0][1].endswith("08-14-2026.pdf")


def test_impossible_dates_are_skipped():
    assert reports_collect.list_reports(
        '<a href="https://littlerock.gov/wp-content/uploads/13-45-2026.pdf">x</a>') == []
