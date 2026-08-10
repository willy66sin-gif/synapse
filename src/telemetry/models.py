"""
Device registry model -- maps a device_id to the Ed25519 public key
Synapse verifies its telemetry signatures against.

Container only: this table intentionally ships with zero rows, same
discipline as src/intake/models.py's IdentityCrosswalkEntry. No real or
placeholder device is registered -- see the 2026-08-10
device-telemetry-trust design conversation for why (there is no
telemetry ingestion pathway anywhere in this codebase yet for a real
device to feed; see src/telemetry/trust.py's module docstring).

Fixed to Ed25519, not a pluggable algorithm column: this is the one
scheme this design targets (small keys/signatures, deterministic,
widely supported by real HSMs later), and there is no second algorithm
in sight to justify a pluggable field.

Single mutable row per device_id (like AuthorizedIssuer), not
append-only (unlike AdjudicationAuditEntry) -- key rotation/revocation
history is explicitly not designed here; a future need, not built now.

Only ever stores the *public* key. Verification never needs the
private key, regardless of whether it lives in a software keypair
today or a real HSM/secure element later -- upgrading key custody
later requires no change to this schema or to how Synapse verifies
signatures (src/telemetry/trust.py).

Shares src/core/models.py's Base so src/core/init_db.py's single
create_all() call provisions this table too, same pattern as
src/evidence/models.py, src/supervisor/models.py, and
src/intake/models.py.
"""
from sqlalchemy.orm import Mapped, mapped_column

from src.core.models import Base


class DeviceRegistryEntry(Base):
    __tablename__ = "device_registry"

    device_id: Mapped[str] = mapped_column(primary_key=True)
    public_key: Mapped[str]
    registered_since: Mapped[str]
