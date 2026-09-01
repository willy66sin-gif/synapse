"""
SQLAlchemy model for persisted billing-statement send records
(Hamilton Labs statement-of-accounts, 2026-09-01).

One row per attempted statement send, success or failure -- "every
send gets its own signed evidence record... including failed-send
attempts" per this pass's own instruction. Its own table, not folded
into src/evidence/models.py's AdjudicationAuditEntry, mirrors
src/airlock/models.py's ProfileRejectionAuditEntry: a billing
statement is a record ABOUT a batch of adjudications, not an
adjudication outcome itself -- same "distinct evidence types live in
distinct tables" convention this codebase already follows
(AdjudicationAuditEntry vs. OverrideAuditEntry vs.
ProfileRejectionAuditEntry vs. SensorZoneStateRejectionAuditEntry).

period_start/period_end/delivered are pulled out as their own columns
-- same "the discriminators a reader most needs get their own column"
convention as AdjudicationAuditEntry.decision and
ProfileRejectionAuditEntry.reason_code -- period_end specifically is
what src/billing/service.py's is_period_due() needs to look up without
parsing every row's full record JSON. Stored as ISO 8601 strings, not
a native DateTime column, matching this codebase's existing
convention of never using a native timestamp column anywhere
(AdjudicationAuditEntry itself has no evaluated_at column either --
only inside `record`'s JSON).

Append-only, same discipline as every other audit table here.

Shares src/core/models.py's Base -- src/core/init_db.py needs this
module added to its import list, same as every other domain's audit
table.
"""
from sqlalchemy import JSON
from sqlalchemy.orm import Mapped, mapped_column

from src.core.models import Base


class BillingStatementAuditEntry(Base):
    __tablename__ = "billing_statement_records"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    period_start: Mapped[str]
    period_end: Mapped[str]
    delivered: Mapped[bool]
    record: Mapped[dict] = mapped_column(JSON)
