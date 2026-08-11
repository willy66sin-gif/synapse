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
        )
