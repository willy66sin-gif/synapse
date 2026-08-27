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
from sqlalchemy import JSON
from sqlalchemy.orm import Mapped, mapped_column

from src.core.models import Base


class DeviceRegistryEntry(Base):
    __tablename__ = "device_registry"

    device_id: Mapped[str] = mapped_column(primary_key=True)
    public_key: Mapped[str]
    registered_since: Mapped[str]


class SensorZoneStateAuditEntry(Base):
    """
    Persisted src/evidence/emitter.py's emit_sensor_zone_state_evidence()
    output (2026-08-27, telemetry-ingestion-pathway build) — the
    dedicated evidence trail for verified-telemetry ZoneRecord writes,
    kept separate from src/evidence/models.py's AdjudicationAuditEntry
    and src/supervisor/models.py's OverrideAuditEntry per that build's
    evidence-emission decision.

    Append-only, same reasoning as those two tables: zone_id is not the
    primary key, so every verified write to the same zone over time is
    its own row, never overwritten.

    Lives in src/telemetry/, not src/evidence/, mirroring how
    OverrideAuditEntry lives in src/supervisor/ rather than
    src/evidence/ — each domain owns its own audit table; src/evidence/
    holds only the shared signing mechanism plus the original
    AdjudicationRecord table it was built for.

    Shares src/core/models.py's Base so src/core/init_db.py's single
    create_all() call provisions this table too — src/telemetry/models
    is already in that file's import list (for DeviceRegistryEntry
    above), so no init_db.py change is needed for this table either.
    """

    __tablename__ = "sensor_zone_state_records"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    zone_id: Mapped[str] = mapped_column(index=True)
    device_id: Mapped[str]
    record: Mapped[dict] = mapped_column(JSON)


class SensorZoneStateRejectionAuditEntry(Base):
    """
    Persisted src/evidence/emitter.py's
    emit_sensor_zone_rejection_evidence() output (2026-08-27,
    telemetry-rejection-evidence addendum) — audit trail for a
    *rejected* verified-telemetry write attempt (unregistered device
    or invalid signature). Kept as its own table rather than a status
    column on SensorZoneStateAuditEntry above: that table's own
    docstring and every row it holds represent writes that actually
    happened (Redis was updated); a rejection is precisely a write
    that did NOT happen, and folding both into one table would blur
    that distinction for every future reader. This mirrors the
    existing convention in this codebase of keeping distinct evidence
    types in distinct tables (AdjudicationAuditEntry vs.
    OverrideAuditEntry — two tables, not one with a type column) rather
    than introducing a new one.

    Append-only, same reasoning as every other audit table here:
    zone_id is not the primary key, so repeated rejected attempts
    against the same zone are each their own row, never overwritten.

    reason_code is its own column, not left buried only in `record`'s
    JSON — mirrors AdjudicationAuditEntry's `decision` column and
    OverrideAuditEntry's `issuer_id` column: pulling out the one
    discriminator a reader most needs is this codebase's existing
    schema convention, not a new one introduced here. No query
    endpoint is added anywhere in this pass — see this addendum's
    handoff's explicit scope.

    Shares src/core/models.py's Base; src/telemetry/models is already
    in src/core/init_db.py's import list (for DeviceRegistryEntry,
    above), so no init_db.py change is needed for this table either.
    """

    __tablename__ = "sensor_zone_state_rejection_records"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    zone_id: Mapped[str] = mapped_column(index=True)
    device_id: Mapped[str]
    reason_code: Mapped[str]
    record: Mapped[dict] = mapped_column(JSON)
