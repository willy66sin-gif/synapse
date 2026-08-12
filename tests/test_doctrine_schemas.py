"""
DoctrineSubmission schema tests (src/doctrine/schemas.py).

Confirms the submission shape validates end to end -- required fields,
extra="forbid", and the jurisdiction_code tag. Does not test a
grounding check or any review outcome; that logic doesn't exist (see
src/doctrine/schemas.py's module docstring) and no test here simulates
one.
"""
from datetime import datetime

import pytest
from pydantic import ValidationError

from src.doctrine.schemas import DoctrineSubmission


def test_a_complete_submission_is_valid():
    submission = DoctrineSubmission(
        submission_id="SUB-0001",
        submitting_party_id="Acme Architects",
        jurisdiction_code="SG",
        citations=["SS EN 1992-1-1", "SS 555:2016"],
        ambiguity_resolution_notes="Local wind-load clause interpreted per SCDF guidance letter dated 2026-06-01.",
        submitted_at=datetime(2026, 8, 12, 9, 30, 0),
        signed_off=True,
    )

    assert submission.jurisdiction_code == "SG"
    assert submission.signed_off is True
    assert submission.citations == ["SS EN 1992-1-1", "SS 555:2016"]


def test_missing_required_field_is_rejected():
    with pytest.raises(ValidationError):
        DoctrineSubmission(
            submission_id="SUB-0002",
            submitting_party_id="Acme Architects",
            jurisdiction_code="SG",
            citations=[],
            ambiguity_resolution_notes="",
            # submitted_at omitted
            signed_off=False,
        )


def test_unknown_field_is_rejected():
    with pytest.raises(ValidationError):
        DoctrineSubmission(
            submission_id="SUB-0003",
            submitting_party_id="Acme Architects",
            jurisdiction_code="SG",
            citations=[],
            ambiguity_resolution_notes="",
            submitted_at=datetime(2026, 8, 12),
            signed_off=False,
            review_status="APPROVED",
        )
