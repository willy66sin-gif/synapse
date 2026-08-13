"""
SubmissionElementSpec schema tests (src/ifc_sg/schemas.py).

Confirms the container shape validates end to end -- required fields,
extra="forbid". Does not test any real IFC/IFC+SG field name or claim
a "complete" SGPset_ catalogue for any element type; that data doesn't
exist in this repo (see src/ifc_sg/schemas.py's module docstring) and
no test here fabricates one.
"""
import pytest
from pydantic import ValidationError

from src.ifc_sg.schemas import SubmissionElementSpec


def test_a_complete_element_spec_is_valid():
    spec = SubmissionElementSpec(
        element_spec_id="SPEC-0001",
        element_type="door",
        jurisdiction_code="SG",
        required_pset_fields=["SGPset_Door"],
    )

    assert spec.element_type == "door"
    assert spec.jurisdiction_code == "SG"
    assert spec.required_pset_fields == ["SGPset_Door"]


def test_missing_required_field_is_rejected():
    with pytest.raises(ValidationError):
        SubmissionElementSpec(
            element_spec_id="SPEC-0002",
            element_type="wall",
            jurisdiction_code="SG",
            # required_pset_fields omitted
        )


def test_unknown_field_is_rejected():
    with pytest.raises(ValidationError):
        SubmissionElementSpec(
            element_spec_id="SPEC-0003",
            element_type="space",
            jurisdiction_code="SG",
            required_pset_fields=[],
            validation_status="COMPLIANT",
        )
