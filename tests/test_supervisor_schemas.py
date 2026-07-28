"""
OverrideRecord schema tests.

Fail-closed like tests/test_airlock.py: valid construction, missing
justification (absent and empty-string), extra fields forbidden.
"""
import pytest
from pydantic import ValidationError

from src.supervisor.schemas import OverrideRecord

VALID_OVERRIDE = {
    "claim_id": "CLM-102",
    "issuer_id": "USR-SUP-01",
    "justification": "Zone re-inspected and cleared by site safety officer.",
    "timestamp": "2026-07-28T12:00:00Z",
}


def test_valid_override_accepted():
    override = OverrideRecord(**VALID_OVERRIDE)

    assert override.claim_id == "CLM-102"
    assert override.issuer_id == "USR-SUP-01"


def test_missing_justification_field_rejected():
    payload = dict(VALID_OVERRIDE)
    del payload["justification"]

    with pytest.raises(ValidationError):
        OverrideRecord(**payload)


def test_empty_justification_rejected():
    """An override with no stated reason is not a real justification."""
    payload = dict(VALID_OVERRIDE)
    payload["justification"] = ""

    with pytest.raises(ValidationError):
        OverrideRecord(**payload)


def test_extra_fields_forbidden():
    payload = dict(VALID_OVERRIDE)
    payload["approved_by_committee"] = True

    with pytest.raises(ValidationError):
        OverrideRecord(**payload)


def test_missing_claim_id_rejected():
    payload = dict(VALID_OVERRIDE)
    del payload["claim_id"]

    with pytest.raises(ValidationError):
        OverrideRecord(**payload)
