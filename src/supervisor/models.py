"""
SQLAlchemy model for persisted override records.

Named OverrideAuditEntry, not OverrideRecord, to avoid confusion with
src/supervisor/schemas.py's OverrideRecord (the validated input
schema) — this is the persisted row, a distinct thing.

Append-only, same reasoning as src/evidence/models.py's
AdjudicationAuditEntry: claim_id is not the primary key, so multiple
override attempts against the same claim over time are each their own
row, never overwritten.

Shares src/core/models.py's Base so src/core/init_db.py's single
create_all() call provisions this table too.
"""
from sqlalchemy import JSON
from sqlalchemy.orm import Mapped, mapped_column

from src.core.models import Base


class OverrideAuditEntry(Base):
    __tablename__ = "override_records"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    claim_id: Mapped[str] = mapped_column(index=True)
    issuer_id: Mapped[str]
    record: Mapped[dict] = mapped_column(JSON)
