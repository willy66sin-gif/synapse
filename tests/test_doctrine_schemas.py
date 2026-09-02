"""
DoctrineSubmission schema tests (src/doctrine/schemas.py).

Confirms the submission shape validates end to end -- required fields,
extra="forbid", and the jurisdiction_code tag. Does not test a
grounding check or any review outcome; that logic doesn't exist (see
src/doctrine/schemas.py's module docstring) and no test here simulates
one.

CORENET X Parallel Entry, Tier 2 (2026-09-02): test_a_complete_submission_is_valid
now also supplies the five new fields (corenet_x_reference,
corenet_x_gateway, corenet_x_approval_date, entered_by, and the
deliberately-omitted receipt_timestamp) -- receipt_timestamp is left
unset (None default) since a client never supplies it; its rejection
when a client does is covered by
test_client_supplied_receipt_timestamp_is_rejected below.
"""
from datetime import date, datetime

import pytest
from pydantic import ValidationError

from src.doctrine.schemas import CorenetXGateway, DoctrineSubmission


def test_a_complete_submission_is_valid():
    submission = DoctrineSubmission(
        submission_id="SUB-0001",
        submitting_party_id="Acme Architects",
        jurisdiction_code="SG",
        citations=["SS EN 1992-1-1", "SS 555:2016"],
        ambiguity_resolution_notes="Local wind-load clause interpreted per SCDF guidance letter dated 2026-06-01.",
        submitted_at=datetime(2026, 8, 12, 9, 30, 0),
        signed_off=True,
        corenet_x_reference="CNX-2026-00417",
        corenet_x_gateway=CorenetXGateway.DESIGN,
        corenet_x_approval_date=date(2026, 8, 1),
        entered_by="QP",
    )

    assert submission.jurisdiction_code == "SG"
    assert submission.signed_off is True
    assert submission.citations == ["SS EN 1992-1-1", "SS 555:2016"]
    assert submission.corenet_x_gateway == CorenetXGateway.DESIGN
    assert submission.corenet_x_approval_date == date(2026, 8, 1)
    assert submission.entered_by == "QP"
    assert submission.receipt_timestamp is None


def test_client_supplied_receipt_timestamp_is_rejected():
    """
    receipt_timestamp is server-set (src/doctrine/router.py) -- a
    request that supplies its own value is malformed input under this
    file's fail-closed doctrine, rejected at the schema boundary.
    """
    with pytest.raises(ValidationError):
        DoctrineSubmission(
            submission_id="SUB-0004",
            submitting_party_id="Acme Architects",
            jurisdiction_code="SG",
            citations=[],
            ambiguity_resolution_notes="n/a",
            submitted_at=datetime(2026, 8, 12, 9, 30, 0),
            signed_off=True,
            corenet_x_reference="CNX-2026-00417",
            corenet_x_gateway=CorenetXGateway.DESIGN,
            corenet_x_approval_date=date(2026, 8, 1),
            entered_by="QP",
            receipt_timestamp=datetime(2026, 8, 12, 9, 30, 0),
        )


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
