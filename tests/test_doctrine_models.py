"""
Doctrine submission container tests (src/doctrine/models.py).

Confirms the table exists and provisions cleanly -- and, same
discipline as src/intake/models.py's IdentityCrosswalkEntry, that it
ships with zero rows. Uses a throwaway in-memory sqlite engine (sync,
stdlib sqlite3 -- no new dependency) purely to run create_all() and
count rows; this is not the app's real Postgres engine and nothing
here touches src/core/init_db.py's runtime path.
"""
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from src.core.models import Base
from src.doctrine.models import DoctrineSubmissionRecord


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
    }
