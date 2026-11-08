"""
SQLAlchemy model for persisted adjudication evidence.

Fulfills the audit-storage half of CLAUDE.md's Technology Stack line
for PostgreSQL, which was declared from the start but never actually
implemented until now — emit_evidence() stayed a pure, non-persisting
function; this is the first thing that writes its output anywhere.

Append-only by application convention only -- no database constraint,
trigger or grant stops a row being updated or deleted -- matching the
emitter's own "append-only audit-trail logging" language: claim_id is NOT the primary key, so re-adjudicating
the same claim_id (however that might happen) adds a new row rather
than overwriting one. fetch_latest_adjudication_record() reads the
most recent row for a given claim_id.

Shares src/core/models.py's Base so src/core/init_db.py's single
create_all() call provisions this table too.
"""
from sqlalchemy import JSON
from sqlalchemy.orm import Mapped, mapped_column

from src.core.models import Base


class AdjudicationAuditEntry(Base):
    __tablename__ = "adjudication_records"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    claim_id: Mapped[str] = mapped_column(index=True)
    decision: Mapped[str]
    record: Mapped[dict] = mapped_column(JSON)
