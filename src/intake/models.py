"""
Identity crosswalk model -- maps an external system's issuer/zone
identifiers to Synapse's own issuer_id/zone_id.

Container only: this table intentionally ships with zero rows. Which
partner/ePTW vendor gets integrated, and what its ID formats even look
like, hasn't been decided -- populating this with placeholder rows
would be exactly the "fabricated crosswalk that fails R-AUTH
silently-wrong" risk already flagged and ruled out when this adapter
was scoped (see src/intake/adapters/eptw.py's module docstring).
Structure only, same discipline src/core/models.py's IssuerRole
migration already established for this codebase (schema ships ahead
of the data that populates it, deliberately, when the data isn't real
yet).

One table serves both issuer and zone lookups (external_id_type
discriminates) rather than two near-identical schemas, since the
lookup shape -- "this source system's ID means this Synapse ID" -- is
identical either way; only what synapse_id refers to differs.

Deliberately NOT scoped to authority_level (role/clearance
translation): that isn't an identity lookup (external_id -> a
different but equally-opaque Synapse ID). It's a small, closed
vocabulary translation -- source role name -> Synapse integer
clearance level -- structurally closer to src/intake/adapters/eptw.py's
WORK_TYPE_MAP than to this table. See that module's docstring for
where it's expected to land instead.

Shares src/core/models.py's Base so src/core/init_db.py's single
create_all() call provisions this table too, same pattern as
src/evidence/models.py and src/supervisor/models.py.
"""
from enum import Enum

from sqlalchemy import Enum as SAEnum
from sqlalchemy import UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.core.models import Base


class ExternalIdType(str, Enum):
    ISSUER = "issuer"
    ZONE = "zone"


class IdentityCrosswalkEntry(Base):
    """
    One row per (source_system, external_id, external_id_type) ->
    synapse_id. All plain strings -- deliberately not shaped around
    any specific ePTW/permit vendor's ID format, since no real vendor
    has been identified (see this module's own docstring).
    """

    __tablename__ = "identity_crosswalk"
    __table_args__ = (
        UniqueConstraint(
            "source_system", "external_id", "external_id_type",
            name="uq_identity_crosswalk_lookup_key",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source_system: Mapped[str] = mapped_column(index=True)
    external_id: Mapped[str]
    external_id_type: Mapped[ExternalIdType] = mapped_column(SAEnum(ExternalIdType))
    synapse_id: Mapped[str]
