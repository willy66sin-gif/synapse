"""
Regulatory instrument registry model (Step 1, 2026-10-11).

Container only: this table ships with zero rows, same discipline as
src/doctrine/models.py's DoctrineSubmissionRecord and
src/profiles/models.py's CertifiedProfileRecord. No real instrument is
seeded here -- a row exists only once someone holding the actual
document enters it through src/regulatory/router.py's
POST /regulatory/instruments. That includes the BCA advisory
src/core/rules.py's lift-panel rule cites: the rule names its
reference number, but no row for it is fabricated in this repo.

(issuing_authority, reference_number) is unique: the same board
cannot have the same reference registered twice under two different
instrument_ids. A second attempt surfaces as IntegrityError, which the
router turns into 409 alongside a repeated instrument_id.

receipt_timestamp is DateTime(timezone=True), unlike
src/doctrine/models.py's bare Mapped[datetime]: the router sets it
from datetime.now(timezone.utc), an aware value, and an aware datetime
needs a timezone-aware column to round-trip without being rejected or
silently stripped by the driver.

Derived values (staleness, days until effective) are deliberately NOT
columns -- computed at creation and carried only inside the hashed
receipt record, same "derived, not stored" rule as
src/doctrine/models.py's staleness.

RegulatoryInstrumentReceiptAuditEntry is a distinct, second table:
hashed evidence of the creation event itself, same "distinct evidence
types live in distinct tables" convention as
DoctrineSubmissionReceiptAuditEntry.

Shares src/core/models.py's Base so src/core/init_db.py's single
create_all() call provisions these tables too.
"""
from datetime import date, datetime

from sqlalchemy import JSON, DateTime, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from src.core.models import Base
from src.doctrine.schemas import GroundingCategory


class RegulatoryInstrumentRecord(Base):
    __tablename__ = "regulatory_instruments"
    __table_args__ = (UniqueConstraint("issuing_authority", "reference_number"),)

    instrument_id: Mapped[str] = mapped_column(primary_key=True)
    issuing_authority: Mapped[str] = mapped_column(index=True)
    reference_number: Mapped[str] = mapped_column(index=True)
    publish_date: Mapped[date]
    effective_date: Mapped[date]
    grounding_category: Mapped[GroundingCategory] = mapped_column(SAEnum(GroundingCategory))
    receipt_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RegulatoryInstrumentReceiptAuditEntry(Base):
    """
    One row per successful RegulatoryInstrument creation -- the hashed
    evidence record src/regulatory/router.py emits on every
    POST /regulatory/instruments. Append-only, same discipline as every
    other audit table here.

    issuing_authority is pulled out as its own column (the
    discriminator a reader most likely filters on), same convention as
    DoctrineSubmissionReceiptAuditEntry.corenet_x_gateway -- the full
    instrument stays inside `record`'s JSON.
    """

    __tablename__ = "regulatory_instrument_receipt_records"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    instrument_id: Mapped[str] = mapped_column(index=True)
    issuing_authority: Mapped[str]
    record: Mapped[dict] = mapped_column(JSON)
