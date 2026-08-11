"""
Certified profile parameter resolution tests (src/core/profile_resolution.py).

Unit style, no I/O -- mirrors tests/test_core_eptw.py's approach to
verify_ptw_precondition: pure function, already-fetched CertifiedProfile
records passed in directly, no repository/session involved.
"""
import pytest

from src.core.profile_resolution import (
    BaseProfileMismatchError,
    BaseProfileMissingError,
    resolve_effective_parameters,
)
from src.profiles.schemas import BaseProfileRef, CertifiedProfile, ProfileLineage

BASE = CertifiedProfile(
    profile_id="EUROCODE-EC2-1-1",
    jurisdiction_code="EU",
    version="2004+A1:2014",
    lineage=ProfileLineage.STANDALONE,
    parameters={"partial_safety_factor": 1.5, "concrete_class": "C25/30"},
)

ANNEX = CertifiedProfile(
    profile_id="DE-EC2-ANNEX",
    jurisdiction_code="DE",
    version="2024",
    lineage=ProfileLineage.BASE_ANNEX,
    base_ref=BaseProfileRef(base_profile_id="EUROCODE-EC2-1-1", base_profile_version="2004+A1:2014"),
    parameters={"partial_safety_factor": 1.35},
)


def test_standalone_profile_returns_its_own_parameters_unchanged():
    result = resolve_effective_parameters(BASE, base=None)

    assert result == {"partial_safety_factor": 1.5, "concrete_class": "C25/30"}


def test_base_annex_profile_merges_with_annex_values_winning_on_collision():
    result = resolve_effective_parameters(ANNEX, base=BASE)

    assert result == {"partial_safety_factor": 1.35, "concrete_class": "C25/30"}


def test_base_annex_profile_with_no_base_supplied_raises_missing_error():
    with pytest.raises(BaseProfileMissingError):
        resolve_effective_parameters(ANNEX, base=None)


def test_base_annex_profile_with_wrong_base_supplied_raises_mismatch_error():
    wrong_base = CertifiedProfile(
        profile_id="EUROCODE-EC3-1-1",
        jurisdiction_code="EU",
        version="2005",
        lineage=ProfileLineage.STANDALONE,
        parameters={"partial_safety_factor": 1.0},
    )

    with pytest.raises(BaseProfileMismatchError):
        resolve_effective_parameters(ANNEX, base=wrong_base)


def test_base_annex_profile_with_correct_id_but_wrong_pinned_version_raises_mismatch_error():
    stale_base = CertifiedProfile(
        profile_id="EUROCODE-EC2-1-1",
        jurisdiction_code="EU",
        version="1992-1-1",  # earlier, unpinned version -- must not silently resolve against it
        lineage=ProfileLineage.STANDALONE,
        parameters={"partial_safety_factor": 1.2},
    )

    with pytest.raises(BaseProfileMismatchError):
        resolve_effective_parameters(ANNEX, base=stale_base)
