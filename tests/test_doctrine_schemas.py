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

Doctrine Grounding Standard, Step 0 (2026-10-11): citations are now
tagged Citation entries (GroundingCategory + reference), at least one
required. The category values used on fixtures here (ACCREDITED_BODY
for the two SS references) are fixture data exercising the structural
gate only -- not a claim about how those standards are actually
classified; nothing in this repo decides that.
"""
from datetime import date, datetime

import pytest
from pydantic import ValidationError

from src.doctrine.schemas import Citation, CorenetXGateway, DoctrineSubmission, GroundingCategory

_ONE_CITATION = [{"category": "STATUTE", "reference": "Example Act s 1"}]


def test_a_complete_submission_is_valid():
    submission = DoctrineSubmission(
        submission_id="SUB-0001",
        submitting_party_id="Acme Architects",
        jurisdiction_code="SG",
        citations=[
            Citation(category=GroundingCategory.ACCREDITED_BODY, reference="SS EN 1992-1-1"),
            Citation(category=GroundingCategory.ACCREDITED_BODY, reference="SS 555:2016"),
        ],
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
    assert [citation.reference for citation in submission.citations] == ["SS EN 1992-1-1", "SS 555:2016"]
    assert submission.citations[0].category == GroundingCategory.ACCREDITED_BODY
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
            citations=_ONE_CITATION,
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
            citations=_ONE_CITATION,
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
            citations=_ONE_CITATION,
            ambiguity_resolution_notes="",
            submitted_at=datetime(2026, 8, 12),
            signed_off=False,
            review_status="APPROVED",
        )


def _submission_fields(**overrides):
    fields = dict(
        submission_id="SUB-0005",
        submitting_party_id="Acme Architects",
        jurisdiction_code="SG",
        citations=_ONE_CITATION,
        ambiguity_resolution_notes="n/a",
        submitted_at=datetime(2026, 8, 12, 9, 30, 0),
        signed_off=True,
        corenet_x_reference="CNX-2026-00417",
        corenet_x_gateway=CorenetXGateway.DESIGN,
        corenet_x_approval_date=date(2026, 8, 1),
        entered_by="QP",
    )
    fields.update(overrides)
    return fields


def test_grounding_category_is_exactly_the_four_named_values():
    """Pins the enum: adding or renaming a category is a doctrine decision, not a silent edit."""
    assert {category.value for category in GroundingCategory} == {
        "STATUTE",
        "ACCREDITED_BODY",
        "PEER_REVIEWED_CASE_LAW",
        "IHL",
    }


@pytest.mark.parametrize("category", [category.value for category in GroundingCategory])
def test_each_grounding_category_is_accepted(category):
    submission = DoctrineSubmission(
        **_submission_fields(citations=[{"category": category, "reference": "Example reference"}])
    )

    assert submission.citations[0].category == GroundingCategory(category)


def test_untagged_freeform_string_citation_is_rejected():
    """The pre-Step-0 shape (a bare string per citation) no longer validates."""
    with pytest.raises(ValidationError):
        DoctrineSubmission(**_submission_fields(citations=["SS EN 1992-1-1"]))


def test_citation_missing_category_is_rejected():
    with pytest.raises(ValidationError):
        DoctrineSubmission(**_submission_fields(citations=[{"reference": "SS EN 1992-1-1"}]))


def test_citation_with_unknown_category_is_rejected():
    """No catch-all: a category outside the four fails closed rather than being filed under OTHER."""
    with pytest.raises(ValidationError):
        DoctrineSubmission(**_submission_fields(citations=[{"category": "CIRCULAR", "reference": "X"}]))


@pytest.mark.parametrize("reference", ["", "   "])
def test_citation_with_blank_reference_is_rejected(reference):
    with pytest.raises(ValidationError):
        DoctrineSubmission(**_submission_fields(citations=[{"category": "STATUTE", "reference": reference}]))


def test_citation_missing_reference_is_rejected():
    with pytest.raises(ValidationError):
        DoctrineSubmission(**_submission_fields(citations=[{"category": "STATUTE"}]))


def test_citation_with_unknown_field_is_rejected():
    with pytest.raises(ValidationError):
        DoctrineSubmission(
            **_submission_fields(citations=[{"category": "STATUTE", "reference": "X", "verified": True}])
        )


def test_empty_citations_list_is_rejected():
    """At least one citation must be present -- see the comment on DoctrineSubmission.citations."""
    with pytest.raises(ValidationError):
        DoctrineSubmission(**_submission_fields(citations=[]))


def test_one_untagged_citation_among_tagged_ones_rejects_the_whole_submission():
    with pytest.raises(ValidationError):
        DoctrineSubmission(
            **_submission_fields(citations=[{"category": "STATUTE", "reference": "Example Act s 1"}, "SS 555:2016"])
        )
