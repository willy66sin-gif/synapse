"""
SQLAlchemy model for persisted profile_id rejection records.

GO Freshness Phase 3a, Part A (2026-08-31): audit trail for a claim
rejected at the Airlock stage because its profile_id requirement
failed (missing or unresolvable) while
src/config.py's Settings.profile_id_enforcement_enabled was True --
see src/airlock/profile_check.py's module docstring for the full
design. Kept as its own table, not folded into
src/evidence/models.py's AdjudicationAuditEntry: every row in that
table represents a claim Core actually adjudicated (a real Verdict was
produced); a profile_id rejection happens before adjudicate() is ever
called, so there is no Verdict to attach it to. Mirrors
src/telemetry/models.py's SensorZoneStateRejectionAuditEntry exactly --
same "distinct evidence types live in distinct tables" convention this
codebase already follows (AdjudicationAuditEntry vs. OverrideAuditEntry
vs. SensorZoneStateRejectionAuditEntry).

Append-only, same reasoning as every other audit table here: claim_id
is not the primary key, so repeated rejected attempts against the same
claim_id over time are each their own row, never overwritten.

reason_code and profile_id are their own columns, not left buried only
in `record`'s JSON -- mirrors AdjudicationAuditEntry's `decision`
column and SensorZoneStateRejectionAuditEntry's `reason_code` column:
pulling out the discriminators a reader most needs is this codebase's
existing schema convention, not a new one introduced here.

Shares src/core/models.py's Base -- src/core/init_db.py needs this
module added to its import list (src/airlock previously had no
models.py at all), same as every other domain's audit table.
"""
from typing import Optional

from sqlalchemy import JSON
from sqlalchemy.orm import Mapped, mapped_column

from src.core.models import Base


class ProfileRejectionAuditEntry(Base):
    __tablename__ = "profile_rejection_records"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    claim_id: Mapped[str] = mapped_column(index=True)
    profile_id: Mapped[Optional[str]]
    reason_code: Mapped[str]
    record: Mapped[dict] = mapped_column(JSON)
