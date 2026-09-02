"""
Doctrine submission container tests (src/doctrine/models.py).

Confirms the table exists and provisions cleanly -- and, same
discipline as src/intake/models.py's IdentityCrosswalkEntry, that it
ships with zero rows (still true after the Tier 2 CORENET X Parallel
Entry build: zero rows is about seed data, not about whether
src/doctrine/router.py's endpoint can ever write one). Uses a throwaway
in-memory sqlite engine (sync, stdlib sqlite3 -- no new dependency)
purely to run create_all() and count rows; this is not the app's real
Postgres engine and nothing here touches src/core/init_db.py's runtime
path.
"""
from datetime import date, datetime

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from src.core.models import Base
from src.doctrine.models import CorenetXGateway, DoctrineSubmissionReceiptAuditEntry, DoctrineSubmissionRecord


def test_doctrine_submissions_table_provisions_with_zero_rows():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[DoctrineSubmissionRecord.__table__])

    with Session(engine) as session:
        count = session.scalar(select(func.count()).select_from(DoctrineSubmissionRecord))

    assert count == 0


def test_doctrine_submission_record_has_the_expected_columns():
    column_names = {column.name for column in DoctrineSubmissionRecord.__table__.columns}

    assert column_names == {
        "submission_id",
        "submitting_party_id",
        "jurisdiction_code",
        "citations",
        "ambiguity_resolution_notes",
        "submitted_at",
        "signed_off",
        "corenet_x_reference",
        "corenet_x_gateway",
        "corenet_x_approval_date",
        "receipt_timestamp",
        "entered_by",
    }


def test_doctrine_submission_record_accepts_a_real_row():
    """
    Provisions and inserts one full row -- confirms the new columns'
    types (SAEnum(CorenetXGateway), date, datetime) round-trip cleanly
    through a real (in-memory) database, not just through Pydantic.
    """
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[DoctrineSubmissionRecord.__table__])

    with Session(engine) as session:
        session.add(
            DoctrineSubmissionRecord(
                submission_id="SUB-0001",
                submitting_party_id="Acme Architects",
                jurisdiction_code="SG",
                citations=["SS EN 1992-1-1"],
                ambiguity_resolution_notes="n/a",
                submitted_at=datetime(2026, 8, 12, 9, 30, 0),
                signed_off=True,
                corenet_x_reference="CNX-2026-00417",
                corenet_x_gateway=CorenetXGateway.DESIGN,
                corenet_x_approval_date=date(2026, 8, 1),
                receipt_timestamp=datetime(2026, 8, 12, 9, 30, 0),
                entered_by="QP",
            )
        )
        session.commit()

        row = session.scalar(select(DoctrineSubmissionRecord))

    assert row.corenet_x_gateway == CorenetXGateway.DESIGN
    assert row.corenet_x_approval_date == date(2026, 8, 1)
    assert row.entered_by == "QP"


def test_doctrine_submission_receipt_audit_entry_has_the_expected_columns():
    column_names = {column.name for column in DoctrineSubmissionReceiptAuditEntry.__table__.columns}

    assert column_names == {"id", "submission_id", "corenet_x_gateway", "record"}
