"""
Strict Pydantic v2 schema for the telemetry HTTP ingestion endpoint
(2026-09-09, telemetry-http-endpoint build).

Same fail-closed discipline as src/airlock/schemas.py's ClaimPayload:
extra="forbid", no optional fields the domain doesn't genuinely allow.
This schema only carries what src/telemetry/zone_write.py's
write_sensor_zone_state() itself takes as parameters — it does not
re-derive or duplicate any of those five values, and it does not
decide sensor-eligibility (that stays a runtime check inside
write_sensor_zone_state() against src/core/rules.py's
SENSOR_ELIGIBLE_ZONE_FIELDS, not a schema-level enum here, so a future
dual-input field remains a one-line addition there rather than a
schema change here).

payload/signature travel over HTTP as base64 text (JSON has no native
bytes type) and are decoded back to raw bytes at the router boundary,
immediately before calling write_sensor_zone_state() — this schema
itself only validates that both decode cleanly, it does not interpret
their contents.
"""
import base64
import binascii

from pydantic import BaseModel, ConfigDict, field_validator


class TelemetryZoneStatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: str
    # Project-scoping boundary (2026-09-15, telemetry follow-on to the
    # Ring-Fencing Concept Note pass -- closes the gap that pass's Open
    # Items entry flagged, see CLAUDE.md's Changelog). Required, no
    # default, same extra="forbid" posture as every other field on this
    # schema and as ClaimPayload.project_id (src/airlock/schemas.py) --
    # a telemetry write must declare which project's zone it targets,
    # same fail-closed discipline as a claim declaring which project it
    # belongs to. Device identity itself stays global/unscoped -- see
    # src/telemetry/trust.py's module docstring for that design fork and
    # why.
    project_id: str
    zone_id: str
    field: str
    value: bool
    payload: str
    signature: str

    @field_validator("payload", "signature")
    @classmethod
    def _must_be_valid_base64(cls, value: str) -> str:
        """
        Fail-closed at the schema boundary, not deep inside
        write_sensor_zone_state(): a body that isn't even valid
        base64 is malformed input, same class of rejection as
        src/airlock/schemas.py's PtwContext datetime validator, not a
        device-trust question this endpoint's dedicated
        R-DEV-01/R-DEV-02 handling should ever see.
        """
        try:
            base64.b64decode(value, validate=True)
        except binascii.Error as exc:
            raise ValueError("must be valid base64") from exc
        return value

    def decoded_payload(self) -> bytes:
        return base64.b64decode(self.payload)

    def decoded_signature(self) -> bytes:
        return base64.b64decode(self.signature)
