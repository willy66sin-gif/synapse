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
active_crane plus, as of 2026-09-10, tagged_asset_present (a
carrier-agnostic RFID-type presence/proximity signal — the physical
carrier is architecturally irrelevant). Deferred to Core's own set
rather than hardcoding field names here, so tagged_asset_present
really was a one-line addition in src/core/rules.py, exactly as this
module's design anticipated — zero changes needed in this file or in
src/core/repository.py's fetch_zone_record()/_resolve_zone_field() to
support it, confirming the 2026-09-09 investigate-only finding that
both were already generic over field name and boolean value.

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
from src.telemetry.repository import (
    fetch_latest_sensor_zone_state_project_id,
    persist_sensor_zone_rejection_record,
    persist_sensor_zone_state_record,
)
from src.telemetry.trust import (
    DeviceNotRegisteredError,
    TelemetrySignatureInvalidError,
    verify_telemetry,
)


async def write_sensor_zone_state(
    session: AsyncSession,
    redis_client: Redis,
    project_id: str,
    device_id: str,
    zone_id: str,
    field: str,
    value: bool,
    payload: bytes,
    signature: bytes,
) -> dict:
    """
    Verifies the telemetry payload, then writes `field` into
    (project_id, zone_id)'s sensor-sourced Redis hash and emits +
    persists a dedicated SensorZoneStateRecord evidence entry showing
    the write came from a verified device.

    project_id (2026-09-15, telemetry project-scoping follow-on):
    threaded straight into src/core/rules.py's
    sensor_zone_redis_key(project_id, zone_id) — see that function's
    own docstring for why cross-project isolation is enforced at the
    key level, not via a separate exception/reason-code check. Also
    threaded into both evidence emitters below, so a reader of the
    audit trail can tell which project a sensor write (or rejection)
    was for. Device verification itself (verify_telemetry(), below)
    does NOT take or check project_id — device identity stays
    global/unscoped, a deliberate design fork; see
    src/telemetry/trust.py's module docstring for the full reasoning.

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

    Device-reassignment anomaly detection (2026-09-15, telemetry
    project-scoping follow-on, part 2 -- Willy-authorized): detection
    only, never enforcement -- a device's declared project_id differing
    from its own most recently recorded successful write (see
    src/telemetry/repository.py's
    fetch_latest_sensor_zone_state_project_id()) does NOT block, delay,
    or fail this write. Device Trust stays global/unscoped, per the
    prior pass's own decision (src/telemetry/trust.py's module
    docstring) -- a device legitimately moving between projects over
    its service life is expected, not an error, so this can only ever
    be a flag, never a rejection. On a mismatch: the resulting
    SensorZoneStateRecord's project_changed_from field is set to the
    prior project_id (see emit_sensor_zone_state_evidence()'s own
    docstring), and a line is printed for immediate operational
    visibility -- same plain print()-to-stdout mechanism this codebase
    already uses for other operational signals (e.g.
    src/airlock/router.py's billing-trigger-failure line), not a new
    logging framework introduced for this one signal. A device's
    first-ever write (no prior SensorZoneStateAuditEntry row at all) is
    NOT an anomaly -- there is nothing to compare against, so
    project_changed_from stays None.
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
            project_id, device_id, zone_id, field, value, exc.reason_code
        )
        await persist_sensor_zone_rejection_record(session, rejection_evidence)
        raise

    prior_project_id = await fetch_latest_sensor_zone_state_project_id(session, device_id)
    project_changed_from = None
    if prior_project_id is not None and prior_project_id != project_id:
        project_changed_from = prior_project_id
        print(
            f"Telemetry device reassignment detected: device_id={device_id!r} previously wrote "
            f"project_id={prior_project_id!r}, now writing project_id={project_id!r} "
            f"(zone_id={zone_id!r}, field={field!r}). Write allowed -- flagged for review, not rejected."
        )

    await redis_client.hset(sensor_zone_redis_key(project_id, zone_id), field, "true" if value else "false")

    evidence = emit_sensor_zone_state_evidence(
        project_id, device_id, zone_id, field, value, project_changed_from=project_changed_from
    )
    await persist_sensor_zone_state_record(session, evidence)
    return evidence
