"""Subdivision resolution: assessor SUBDIV strings -> clerk SUB search terms."""
from pulaski_legal import SubResolver, base_name, norm


def test_norm_strips_punctuation_and_case():
    assert norm("St. Charles  Adn") == "ST CHARLES ADN"
    assert norm(None) == ""
    assert norm("  ") == ""


def test_base_name_strips_addition_and_phase_noise():
    assert base_name("ST CHARLES ADN") == "ST CHARLES"
    assert base_name("CHENAL DOWNS SUB PH 1") == "CHENAL DOWNS"
    assert base_name("WOODRUFF REPLAT") == "WOODRUFF"


def test_base_name_keeps_bare_names():
    assert base_name("KINGWOOD") == "KINGWOOD"


def test_resolver_maps_assessor_variants_to_clerk_terms():
    r = SubResolver()
    # exact family: clerk vocabulary contains ST CHARLES ADN
    assert r.resolve("ST CHARLES ADN") == "ST CHARLES ADN"
    # phase-suffixed assessor form resolves into the same family
    assert r.resolve("ST CHARLES ADN PH VIII") == "ST CHARLES ADN"


def test_resolver_empty_and_unknown():
    r = SubResolver()
    assert r.resolve("") == ""
    assert r.resolve(None) == ""
    # unknown names fall back to their own base prefix (never wrong-family)
    assert r.resolve("ZZYZX ESTATES PH 2") == "ZZYZX ESTATES"
