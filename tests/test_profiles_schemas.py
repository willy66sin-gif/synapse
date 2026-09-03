"""
CertifiedProfile schema tests (src/profiles/schemas.py).

Covers the lineage/base_ref consistency validator directly, unit
style (mirroring tests/test_core_eptw.py's approach to
verify_ptw_precondition) — the two valid shapes (STANDALONE with no
base_ref, BASE_ANNEX with one) plus the two invalid combinations the
validator exists to reject.
"""
import pytest
from pydantic import ValidationError

from src.profiles.schemas import BaseProfileRef, CertifiedProfile, ProfileLineage


def test_standalone_profile_with_no_base_ref_is_valid():
    profile = CertifiedProfile(
        profile_id="SG-BC-2024",
        jurisdiction_code="SG",
        version="2024",
        lineage=ProfileLineage.STANDALONE,
        parameters={"max_span_m": 12.0},
        accountable_architect="Jane Tan, ARB-1234",
    )

    assert profile.base_ref is None


def test_base_annex_profile_with_a_base_ref_is_valid():
    profile = CertifiedProfile(
        profile_id="DE-EC2-ANNEX",
        jurisdiction_code="DE",
        version="2024",
        lineage=ProfileLineage.BASE_ANNEX,
        base_ref=BaseProfileRef(base_profile_id="EUROCODE-EC2-1-1", base_profile_version="2004+A1:2014"),
        parameters={"partial_safety_factor": 1.35},
        accountable_architect="Markus Weber, AKNW-5678",
    )

    assert profile.base_ref.base_profile_id == "EUROCODE-EC2-1-1"


def test_base_annex_profile_with_no_base_ref_is_rejected():
    with pytest.raises(ValidationError, match="BASE_ANNEX profile must set base_ref"):
        CertifiedProfile(
            profile_id="DE-EC2-ANNEX",
            jurisdiction_code="DE",
            version="2024",
            lineage=ProfileLineage.BASE_ANNEX,
            parameters={"partial_safety_factor": 1.35},
            accountable_architect="Markus Weber, AKNW-5678",
        )


def test_standalone_profile_with_a_base_ref_is_rejected():
    with pytest.raises(ValidationError, match="STANDALONE profile must not set base_ref"):
        CertifiedProfile(
            profile_id="SG-BC-2024",
            jurisdiction_code="SG",
            version="2024",
            lineage=ProfileLineage.STANDALONE,
            base_ref=BaseProfileRef(base_profile_id="EUROCODE-EC2-1-1", base_profile_version="2004+A1:2014"),
            parameters={"max_span_m": 12.0},
            accountable_architect="Jane Tan, ARB-1234",
        )


def test_missing_accountable_architect_is_rejected():
    """Fail-closed, same pattern as jurisdiction_code (see
    tests/test_airlock.py's test_missing_required_field_rejected):
    accountable_architect is required, no default -- a Certified
    Profile with no declared architect is rejected at the schema
    boundary, not silently defaulted, since src/maestro/directory.py's
    resolve_pa_authority() reads this field directly at claim
    resolution time."""
    with pytest.raises(ValidationError):
        CertifiedProfile(
            profile_id="SG-BC-2024",
            jurisdiction_code="SG",
            version="2024",
            lineage=ProfileLineage.STANDALONE,
            parameters={"max_span_m": 12.0},
        )
