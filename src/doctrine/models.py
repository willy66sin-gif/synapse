"""
Doctrine submission registry model.

Container only: this table intentionally ships with zero rows, same
discipline as src/intake/models.py's IdentityCrosswalkEntry and
src/profiles/models.py's CertifiedProfileRecord. No real submission is
seeded here -- see src/doctrine/schemas.py's module docstring for why
(the grounding check a submission would need to be reviewed against
doesn't exist yet).

citations stored as JSON (a plain list of strings), not a join table:
mirrors src/profiles/models.py's parameters column -- there is no
structured citation shape to normalize against yet (see
src/doctrine/schemas.py's module docstring).

Shares src/core/models.py's Base so src/core/init_db.py's single
create_all() call provisions this table too, same pattern as
src/evidence/models.py, src/supervisor/models.py, src/intake/models.py,
src/telemetry/models.py, and src/profiles/models.py.
"""
from datetime import datetime

from sqlalchemy import JSON
from sqlalchemy.orm import Mapped, mapped_column

from src.core.models import Base


class DoctrineSubmissionRecord(Base):
    __tablename__ = "doctrine_submissions"

    submission_id: Mapped[str] = mapped_column(primary_key=True)
    submitting_party_id: Mapped[str]
    jurisdiction_code: Mapped[str]
    citations: Mapped[list] = mapped_column(JSON)
    ambiguity_resolution_notes: Mapped[str]
    submitted_at: Mapped[datetime]
    signed_off: Mapped[bool]
