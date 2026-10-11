"""
Certified profile registry model.

Write path (Step 2, 2026-10-11): src/profiles/router.py's
POST /profiles is the first API write path into this table -- before
it, the only thing that ever created a row was
scripts/seed_dev_data.py. The "ships with zero rows" statement below
still holds at seed time; rows now arrive at request time.

Container only: this table intentionally ships with zero rows, same
discipline as src/telemetry/models.py's DeviceRegistryEntry and
src/intake/models.py's IdentityCrosswalkEntry. No real jurisdiction,
regulator, or base-code data (no actual Eurocode parts, no actual
Singapore profile values) is fabricated here — see
src/profiles/schemas.py's module docstring for the two lineage
patterns this structure exists to hold once real profile data is
onboarded.

One table serves both standalone profiles and base codes that other
profiles annex: a BASE_ANNEX row's base_profile_id/base_profile_version
point at another row in this same table (a STANDALONE row, in the
Eurocode-part case), rather than a separate "base code" table existing
alongside it — the lookup shape is identical either way, only whether
anything points at it differs.

base_profile_id / base_profile_version are nullable together: NULL for
STANDALONE rows, both set for BASE_ANNEX rows. That pairing is enforced
at the Pydantic boundary (src/profiles/schemas.py's
CertifiedProfile._lineage_matches_base_ref), not by a DB constraint —
same division of labor CLAUDE.md already draws between Airlock
(validation) and the registry tables it backs.

Shares src/core/models.py's Base so src/core/init_db.py's single
create_all() call provisions this table too, same pattern as
src/evidence/models.py, src/supervisor/models.py, src/intake/models.py,
and src/telemetry/models.py.
"""
from typing import Optional

from sqlalchemy import JSON
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from src.core.models import Base
from src.profiles.schemas import ProfileLineage


class CertifiedProfileRecord(Base):
    __tablename__ = "certified_profiles"

    profile_id: Mapped[str] = mapped_column(primary_key=True)
    # Project-scoping boundary (2026-09-15, Ring-Fencing Concept Note --
    # Willy-authorized implementation). See src/profiles/schemas.py's
    # CertifiedProfile.project_id for why this is a separate field from
    # jurisdiction_code, not an overload of it.
    project_id: Mapped[str]
    jurisdiction_code: Mapped[str]
    version: Mapped[str]
    lineage: Mapped[ProfileLineage] = mapped_column(SAEnum(ProfileLineage))
    base_profile_id: Mapped[Optional[str]]
    base_profile_version: Mapped[Optional[str]]
    parameters: Mapped[dict] = mapped_column(JSON)
    accountable_architect: Mapped[str]


class CertifiedProfileReceiptAuditEntry(Base):
    """
    One row per successful CertifiedProfile creation (Step 2,
    2026-10-11) -- the signed evidence record src/profiles/router.py
    emits on every POST /profiles. Append-only, same discipline as
    every other audit table here, and a distinct table from
    certified_profiles itself, same "distinct evidence types live in
    distinct tables" convention as src/doctrine/models.py's
    DoctrineSubmissionReceiptAuditEntry.

    project_id is pulled out as its own column (the discriminator a
    reader most likely filters on -- including finding every GLOBAL
    profile ever created); the full profile stays inside `record`'s
    JSON.
    """

    __tablename__ = "certified_profile_receipt_records"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    profile_id: Mapped[str] = mapped_column(index=True)
    project_id: Mapped[str]
    record: Mapped[dict] = mapped_column(JSON)
