"""
IFC+SG submission element container tests (src/ifc_sg/models.py).

Confirms the table exists and provisions cleanly -- and, same
discipline as src/doctrine/models.py's DoctrineSubmissionRecord and
src/telemetry/models.py's DeviceRegistryEntry, that it ships with zero
rows. Uses a throwaway in-memory sqlite engine (sync, stdlib sqlite3
-- no new dependency) purely to run create_all() and count rows; this
is not the app's real Postgres engine and nothing here touches
src/core/init_db.py's runtime path.
"""
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from src.core.models import Base
from src.ifc_sg.models import SubmissionElementSpecRecord


def test_ifc_sg_submission_elements_table_provisions_with_zero_rows():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[SubmissionElementSpecRecord.__table__])

    with Session(engine) as session:
        count = session.scalar(select(func.count()).select_from(SubmissionElementSpecRecord))

    assert count == 0


def test_ifc_sg_submission_element_spec_record_has_the_expected_columns():
    column_names = {column.name for column in SubmissionElementSpecRecord.__table__.columns}

    assert column_names == {
        "element_spec_id",
        "element_type",
        "jurisdiction_code",
        "required_pset_fields",
    }
