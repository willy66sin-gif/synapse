"""
HTTP ingestion endpoint for verified device telemetry (2026-09-09,
telemetry-http-endpoint build).

Until this build, src/telemetry/zone_write.py's write_sensor_zone_state()
had no HTTP surface at all — every telemetry-ingestion test called it
directly. This router is that surface: POST /telemetry/zone-state
decodes and validates the request (src/telemetry/schemas.py's
TelemetryZoneStatePayload, same extra="forbid" fail-closed discipline
as src/airlock/schemas.py's ClaimPayload), then calls
write_sensor_zone_state() unchanged — no adjudication, precedence, or
evidence logic lives here; that all stays inside zone_write.py per
this build's explicit scope.

Status-code convention, matched to src/airlock/router.py rather than
invented fresh: that endpoint's own raised-exception rejections
(ProfileIdMissingError/ProfileIdUnresolvableError, from
src/airlock/profile_check.py) surface as HTTPException(422, detail=
{"reason_code": ..., "message": ...}) — not 404/401/403 — because the
request itself is being refused at the boundary, the same way a
malformed Pydantic body is. write_sensor_zone_state()'s two trust
rejections (DeviceNotRegisteredError / TelemetrySignatureInvalidError,
each already carrying its own reason_code — R-DEV-01 / R-DEV-02, see
src/telemetry/trust.py) get the exact same treatment here, so a caller
sees the same {"reason_code", "message"} shape regardless of which
Synapse endpoint rejected the request. TelemetrySignatureInvalidError
is caught ahead of the plain ValueError branch below since it is
itself a ValueError subclass.

A `field` not in SENSOR_ELIGIBLE_ZONE_FIELDS raises a plain ValueError
from write_sensor_zone_state() (a caller-error case, not one of the
two device-trust rejection modes — see that function's own
docstring). Also surfaced as 422, but with no reason_code in the
detail body, since it isn't one of R-DEV-01/R-DEV-02 and inventing a
third code for it is out of this build's scope.

Response shape on success: write_sensor_zone_state()'s own returned
evidence dict (a signed SensorZoneStateRecord), returned as-is — same
pattern as src/airlock/router.py's submit_claim() returning
emit_evidence()'s record directly, no separate response envelope.

Auth beyond the payload signature itself (e.g. a device-level API key
or mTLS on this endpoint, on top of the Ed25519 payload signature) is
a judgment call this build does NOT make — flagged, not decided
silently; see this build's handoff notes.
"""
from fastapi import APIRouter, Depends, HTTPException
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.repository import get_db_session, get_redis_client
from src.telemetry.schemas import TelemetryZoneStatePayload
from src.telemetry.trust import DeviceNotRegisteredError, TelemetrySignatureInvalidError
from src.telemetry.zone_write import write_sensor_zone_state

router = APIRouter(prefix="/telemetry", tags=["telemetry"])


@router.post("/zone-state")
async def submit_zone_state(
    request: TelemetryZoneStatePayload,
    session: AsyncSession = Depends(get_db_session),
    redis_client: Redis = Depends(get_redis_client),
) -> dict:
    """
    FastAPI + Pydantic v2 enforce fail-closed behavior on the request
    shape before this function body runs, same as
    src/airlock/router.py's submit_claim() relies on for ClaimPayload.

    Decodes payload/signature from base64 (TelemetryZoneStatePayload's
    own validators already confirmed both decode cleanly) immediately
    before the single write_sensor_zone_state() call — this handler
    performs no verification, precedence, or evidence logic of its own.
    """
    try:
        evidence = await write_sensor_zone_state(
            session,
            redis_client,
            request.project_id,
            request.device_id,
            request.zone_id,
            request.field,
            request.value,
            request.decoded_payload(),
            request.decoded_signature(),
        )
    except DeviceNotRegisteredError as exc:
        raise HTTPException(
            status_code=422, detail={"reason_code": exc.reason_code, "message": str(exc)}
        ) from exc
    except TelemetrySignatureInvalidError as exc:
        raise HTTPException(
            status_code=422, detail={"reason_code": exc.reason_code, "message": str(exc)}
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return evidence
