"""
Doctrine submission registry model.

Container only: this table intentionally ships with zero rows, same
discipline as src/intake/models.py's IdentityCrosswalkEntry and
src/profiles/models.py's CertifiedProfileRecord. No real submission is
seeded here -- see src/doctrine/schemas.py's module docstring for why
(the grounding check a submission would need to be reviewed against
doesn't exist yet). This changes at request time, not seed time: as of
the Tier 2 CORENET X Parallel Entry build (2026-09-02),
src/doctrine/router.py's POST /doctrine/submissions is the first real
write path into this table -- it stays zero-row only until the first
real submission is filed.

citations stored as JSON (a plain list of strings), not a join table:
mirrors src/profiles/models.py's parameters column -- there is no
structured citation shape to normalize against yet (see
src/doctrine/schemas.py's module docstring).

Shares src/core/models.py's Base so src/core/init_db.py's single
create_all() call provisions this table too, same pattern as
src/evidence/models.py, src/supervisor/models.py, src/intake/models.py,
src/telemetry/models.py, and src/profiles/models.py.

CORENET X Parallel Entry, Tier 2 (2026-09-02): corenet_x_reference/
corenet_x_gateway/corenet_x_approval_date/entered_by mirror
src/doctrine/schemas.py's DoctrineSubmission fields of the same name --
see that module's docstring for why entered_by is a plain str, not a
new role type. receipt_timestamp is set server-side at insert time
(src/doctrine/router.py), never client-supplied. staleness
(receipt_timestamp minus corenet_x_approval_date) is deliberately NOT a
column here -- it is computed at read/evidence time only, per that
build's explicit "derived, not stored" instruction; see
src/evidence/emitter.py's emit_doctrine_submission_evidence().

DoctrineSubmissionReceiptAuditEntry below is a distinct, second table:
signed evidence of the creation event itself, not the submission record
it evidences -- same "distinct evidence types live in distinct tables"
convention as src/billing/models.py's BillingStatementAuditEntry vs.
this file's own DoctrineSubmissionRecord.
"""
from datetime import date, datetime

from sqlalchemy import JSON
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from src.core.models import Base
from src.doctrine.schemas import CorenetXGateway


class DoctrineSubmissionRecord(Base):
    __tablename__ = "doctrine_submissions"

    submission_id: Mapped[str] = mapped_column(primary_key=True)
    submitting_party_id: Mapped[str]
    jurisdiction_code: Mapped[str]
    citations: Mapped[list] = mapped_column(JSON)
    ambiguity_resolution_notes: Mapped[str]
    submitted_at: Mapped[datetime]
    signed_off: Mapped[bool]
    corenet_x_reference: Mapped[str]
    corenet_x_gateway: Mapped[CorenetXGateway] = mapped_column(SAEnum(CorenetXGateway))
    corenet_x_approval_date: Mapped[date]
    receipt_timestamp: Mapped[datetime]
    entered_by: Mapped[str]


class DoctrineSubmissionReceiptAuditEntry(Base):
    """
    One row per successful DoctrineSubmission creation -- the signed
    evidence record src/doctrine/router.py emits on every POST
    /doctrine/submissions, per the Tier 2 CORENET X Parallel Entry
    build's "own signed evidence record on creation" requirement.
    Append-only, same discipline as every other audit table here.

    corenet_x_gateway is pulled out as its own column (the discriminator
    a reader most likely filters on), same convention as
    src/airlock/models.py's ProfileRejectionAuditEntry.reason_code --
    the full submission stays inside `record`'s JSON.
    """

    __tablename__ = "doctrine_submission_receipt_records"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    submission_id: Mapped[str] = mapped_column(index=True)
    corenet_x_gateway: Mapped[str]
    record: Mapped[dict] = mapped_column(JSON)
