"""
Regulatory instrument registry model tests (src/regulatory/models.py).

Same real in-memory SQLite approach as tests/test_doctrine_models.py:
confirms the column set, that the table ships empty, that a full row
round-trips, and that the (issuing_authority, reference_number)
uniqueness the router's 409 depends on is a real constraint.
"""
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.core.models import Base
from src.doctrine.schemas import GroundingCategory
from src.regulatory.models import RegulatoryInstrumentReceiptAuditEntry, RegulatoryInstrumentRecord


def _row(instrument_id="INST-0001", reference_number="EXAMPLE/REF/2026/001"):
    return RegulatoryInstrumentRecord(
        instrument_id=instrument_id,
        issuing_authority="EXAMPLE-BOARD",
        reference_number=reference_number,
        publish_date=date(2026, 9, 1),
        effective_date=date(2026, 10, 1),
        grounding_category=GroundingCategory.STATUTE,
        receipt_timestamp=datetime(2026, 9, 2, 9, 0, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
def engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[RegulatoryInstrumentRecord.__table__])
    return engine


def test_regulatory_instruments_table_ships_empty(engine):
    with Session(engine) as session:
        count = session.scalar(select(func.count()).select_from(RegulatoryInstrumentRecord))

    assert count == 0


def test_regulatory_instrument_record_has_the_expected_columns():
    column_names = {column.name for column in RegulatoryInstrumentRecord.__table__.columns}

    assert column_names == {
        "instrument_id",
        "issuing_authority",
        "reference_number",
        "publish_date",
        "effective_date",
        "grounding_category",
        "receipt_timestamp",
    }


def test_regulatory_instrument_record_accepts_a_real_row(engine):
    with Session(engine) as session:
        session.add(_row())
        session.commit()

        row = session.scalar(select(RegulatoryInstrumentRecord))

    assert row.grounding_category == GroundingCategory.STATUTE
    assert row.publish_date == date(2026, 9, 1)
    assert row.effective_date == date(2026, 10, 1)


def test_same_reference_from_same_authority_cannot_be_registered_twice(engine):
    with Session(engine) as session:
        session.add(_row(instrument_id="INST-0001"))
        session.commit()

        session.add(_row(instrument_id="INST-0002"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_regulatory_instrument_receipt_audit_entry_has_the_expected_columns():
    column_names = {column.name for column in RegulatoryInstrumentReceiptAuditEntry.__table__.columns}

    assert column_names == {"id", "instrument_id", "issuing_authority", "record"}
