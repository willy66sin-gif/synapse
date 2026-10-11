"""
RegulatoryInstrument schema tests (src/regulatory/schemas.py).

Fixture values are deliberately invented placeholders ("EXAMPLE-BOARD",
"EXAMPLE/REF/2026/001") -- not a real board or a real instrument. The
reference keeps "/" separators only because the one real format seen
(BCA's) has them and the schema must not choke on them.
"""
from datetime import date, datetime

import pytest
from pydantic import ValidationError

from src.doctrine.schemas import GroundingCategory
from src.regulatory.schemas import RegulatoryInstrument


def _instrument_fields(**overrides):
    fields = dict(
        instrument_id="INST-0001",
        issuing_authority="EXAMPLE-BOARD",
        reference_number="EXAMPLE/REF/2026/001",
        publish_date=date(2026, 9, 1),
        effective_date=date(2026, 10, 1),
        grounding_category="STATUTE",
    )
    fields.update(overrides)
    return fields


def test_a_complete_instrument_is_valid():
    instrument = RegulatoryInstrument(**_instrument_fields())

    assert instrument.issuing_authority == "EXAMPLE-BOARD"
    assert instrument.reference_number == "EXAMPLE/REF/2026/001"
    assert instrument.publish_date == date(2026, 9, 1)
    assert instrument.effective_date == date(2026, 10, 1)
    assert instrument.grounding_category == GroundingCategory.STATUTE
    assert instrument.receipt_timestamp is None


def test_instrument_has_no_project_id():
    """A standing instrument is not project-scoped: the field does not exist, and supplying one is rejected."""
    assert "project_id" not in RegulatoryInstrument.model_fields

    with pytest.raises(ValidationError):
        RegulatoryInstrument(**_instrument_fields(project_id="PROJ-TEST-01"))


@pytest.mark.parametrize("category", [category.value for category in GroundingCategory])
def test_each_grounding_category_is_accepted(category):
    instrument = RegulatoryInstrument(**_instrument_fields(grounding_category=category))

    assert instrument.grounding_category == GroundingCategory(category)


def test_unknown_grounding_category_is_rejected():
    with pytest.raises(ValidationError):
        RegulatoryInstrument(**_instrument_fields(grounding_category="CIRCULAR"))


@pytest.mark.parametrize(
    "missing",
    ["instrument_id", "issuing_authority", "reference_number", "publish_date", "effective_date", "grounding_category"],
)
def test_each_required_field_is_required(missing):
    fields = _instrument_fields()
    del fields[missing]

    with pytest.raises(ValidationError):
        RegulatoryInstrument(**fields)


@pytest.mark.parametrize("field", ["instrument_id", "issuing_authority", "reference_number"])
@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_identifying_field_is_rejected(field, blank):
    with pytest.raises(ValidationError):
        RegulatoryInstrument(**_instrument_fields(**{field: blank}))


def test_client_supplied_receipt_timestamp_is_rejected():
    """Same rule as DoctrineSubmission: receipt_timestamp is server-set, never client-supplied."""
    with pytest.raises(ValidationError):
        RegulatoryInstrument(**_instrument_fields(receipt_timestamp=datetime(2026, 9, 2, 9, 0, 0)))


def test_effective_date_may_equal_or_precede_publish_date():
    """No ordering between the two dates is enforced -- see the module docstring for why."""
    same_day = RegulatoryInstrument(**_instrument_fields(effective_date=date(2026, 9, 1)))
    earlier = RegulatoryInstrument(**_instrument_fields(effective_date=date(2026, 8, 1)))

    assert same_day.effective_date == same_day.publish_date
    assert earlier.effective_date < earlier.publish_date


def test_unknown_field_is_rejected():
    with pytest.raises(ValidationError):
        RegulatoryInstrument(**_instrument_fields(status="IN_FORCE"))
