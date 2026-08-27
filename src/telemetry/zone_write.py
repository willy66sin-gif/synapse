"""
Verified-telemetry ZoneRecord write path (2026-08-27, telemetry-
ingestion-pathway build).

Wires src/telemetry/trust.py's device-trust verification into a real
caller for the first time — until this build that module was tested
but had nothing to gate (see its own docstring's prior "No real caller
wires into this today"). This module is that caller: verify first,
then write, then emit dedicated evidence.

Writes into the sensor-sourced Redis hash src/core/rules.py's
SENSOR_ELIGIBLE_ZONE_FIELDS/sensor_zone_redis_key() define — distinct
from the human-declared `zone:{zone_id}` hash scripts/seed_dev_data.py
writes. This module does not itself decide precedence between the two;
src/core/repository.py's fetch_zone_record() is what actually prefers
the sensor value at read time. This module's only job is: verify,
write to the sensor hash, emit + persist evidence.

Only fields in SENSOR_ELIGIBLE_ZONE_FIELDS may be written here —
currently just active_crane, the one field this build's handoff names
as dual-input-source. Deferred to Core's own set rather than
hardcoding "active_crane" here, so a second sensor-eligible field is a
one-line addition in src/core/rules.py, not a change to this module.

On verification failure (DeviceNotRegisteredError /
TelemetrySignatureInvalidError, each carrying its own reason_code —
see src/telemetry/trust.py), this function still makes no Redis write
(2026-08-27, telemetry-rejection-evidence addendum: this was
previously also true of evidence emission — it no longer is). A
rejected attempt now gets its own signed, persisted
SensorZoneStateRejectionRecord (src/evidence/emitter.py's
emit_sensor_zone_rejection_evidence(), src/telemetry/models.py's
SensorZoneStateRejectionAuditEntry) before the original exception
propagates, unwrapped and otherwise unchanged — this is an audit
record of the rejection, not a retry, and does not affect the
exception the caller sees.
"""
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.rules import SENSOR_ELIGIBLE_ZONE_FIELDS, sensor_zone_redis_key
from src.evidence.emitter import emit_sensor_zone_rejection_evidence, emit_sensor_zone_state_evidence
from src.telemetry.repository import persist_sensor_zone_rejection_record, persist_sensor_zone_state_record
from src.telemetry.trust import (
    DeviceNotRegisteredError,
    TelemetrySignatureInvalidError,
    verify_telemetry,
)


async def write_sensor_zone_state(
    session: AsyncSession,
    redis_client: Redis,
    device_id: str,
    zone_id: str,
    field: str,
    value: bool,
    payload: bytes,
    signature: bytes,
) -> dict:
    """
    Verifies the telemetry payload, then writes `field` into zone_id's
    sensor-sourced Redis hash and emits + persists a dedicated
    SensorZoneStateRecord evidence entry showing the write came from a
    verified device.

    On verification failure, raises DeviceNotRegisteredError or
    TelemetrySignatureInvalidError, unwrapped and unchanged from
    before this addendum — no Redis write happens either way — but
    first emits + persists a distinct SensorZoneStateRejectionRecord
    carrying the exception's own reason_code, so the rejection itself
    is audited even though nothing was written.

    Raises ValueError if `field` is not in SENSOR_ELIGIBLE_ZONE_FIELDS
    — checked before verification, so an invalid field is rejected
    without even attempting a device/signature lookup, and without any
    evidence emission (this ValueError is a caller-error case, not one
    of the two telemetry-trust rejection modes this addendum covers).
    """
    if field not in SENSOR_ELIGIBLE_ZONE_FIELDS:
        raise ValueError(
            f"'{field}' is not a sensor-eligible ZoneRecord field "
            f"(SENSOR_ELIGIBLE_ZONE_FIELDS={sorted(SENSOR_ELIGIBLE_ZONE_FIELDS)})."
        )

    try:
        await verify_telemetry(session, device_id, payload, signature)
    except (DeviceNotRegisteredError, TelemetrySignatureInvalidError) as exc:
        rejection_evidence = emit_sensor_zone_rejection_evidence(
            device_id, zone_id, field, value, exc.reason_code
        )
        await persist_sensor_zone_rejection_record(session, rejection_evidence)
        raise

    await redis_client.hset(sensor_zone_redis_key(zone_id), field, "true" if value else "false")

    evidence = emit_sensor_zone_state_evidence(device_id, zone_id, field, value)
    await persist_sensor_zone_state_record(session, evidence)
    return evidence
