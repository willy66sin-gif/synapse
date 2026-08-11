"""
Certified profile registry model.

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
    jurisdiction_code: Mapped[str]
    version: Mapped[str]
    lineage: Mapped[ProfileLineage] = mapped_column(SAEnum(ProfileLineage))
    base_profile_id: Mapped[Optional[str]]
    base_profile_version: Mapped[Optional[str]]
    parameters: Mapped[dict] = mapped_column(JSON)
