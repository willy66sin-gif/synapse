"""
Device telemetry trust verification.

Verifies that a telemetry payload genuinely came from the device that
claims to have sent it, unaltered in transit -- inbound only (per the
2026-08-10 design conversation, outbound instruction-authentication to
a physical actuator is a distinct, not-yet-relevant problem: Maestro's
only built output today is human notification, see
src/maestro/adapters/base.py).

Real caller as of the 2026-08-27 telemetry-ingestion-pathway build:
src/telemetry/zone_write.py's write_sensor_zone_state() calls
verify_telemetry() before writing a verified sensor value into
src/core/rules.py's sensor-eligible ZoneRecord fields (see that
module's SENSOR_ELIGIBLE_ZONE_FIELDS/sensor_zone_redis_key()). Before
that build, nothing wrote a zone: key except scripts/seed_dev_data.py,
a manual, optional dev script -- that path is unchanged and remains
the fallback for zones with no registered sensor; this module's
mechanism shipped ahead of that pathway existing, same "build the
container, not the content" discipline as src/intake/models.py's
IdentityCrosswalkEntry.

Two distinct failure modes, deliberately not collapsed into one error
type (mirrors src/intake/adapters/eptw.py's CrosswalkMissError vs.
NotImplementedError distinction):
  - DeviceNotRegisteredError: no DeviceRegistryEntry for this device_id
    at all. The mechanism works; there is no data. A weaker, passive
    signal -- could just be an onboarding gap.
  - TelemetrySignatureInvalidError: the device IS registered, but the
    signature does not verify against its registered public key. A
    stronger, active signal -- possible tampering or a wrong key, not
    just missing data.

Mapping onto CLAUDE.md's two named target-state UX strings (Distinct
Target-State Assurance Failures, Open Items): "Telemetry unavailable"
(STATUS UNAVAILABLE) doesn't correspond to anything here -- that's
about no data arriving at all, a connectivity/liveness problem, out of
this module's scope entirely. TelemetrySignatureInvalidError maps
cleanly to "Cryptographic assurance unknown" (UNVERIFIED SOURCE).
DeviceNotRegisteredError's mapping is an open question, not decided
here -- flagged in the design conversation, not resolved by this file.
"""
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from sqlalchemy.ext.asyncio import AsyncSession

from src.telemetry.repository import fetch_device_public_key

# Reason code convention (src/core/rules.py: R-PTW-01, R-AUTH-01/02/03,
# R-ZONE-01) -- R-<DOMAIN>-<NUMBER>, one per distinct failure class.
# Both telemetry failure modes get their own code (2026-08-27,
# telemetry-ingestion-pathway build -- decision: do not collapse either
# into the other or into the "UNVERIFIED SOURCE" UX placeholder
# string): R-DEV-01 is a provisioning gap (device never entered into
# DeviceRegistryEntry); R-DEV-02 is a trust gap (known device, bad
# signature -- key rotation/tamper investigation, not a registry
# entry). Attached to each exception itself, the same role
# Verdict["reason_code"] plays for Core's adjudication failures --
# src/telemetry/zone_write.py's write_sensor_zone_state() is the real
# caller these are threaded to today: both fail closed with no Redis
# write, and (2026-08-27, telemetry-rejection-evidence addendum) both
# now also get their own signed SensorZoneStateRejectionRecord
# carrying this reason_code, before the exception propagates.
REASON_CODE_DEVICE_NOT_REGISTERED = "R-DEV-01"
REASON_CODE_TELEMETRY_SIGNATURE_INVALID = "R-DEV-02"


class DeviceNotRegisteredError(LookupError):
    """No device_registry entry for this device_id. Mechanism works, no data -- distinct from a verification failure."""

    reason_code = REASON_CODE_DEVICE_NOT_REGISTERED


class TelemetrySignatureInvalidError(ValueError):
    """Device is registered, but the signature does not verify against its registered public key."""

    reason_code = REASON_CODE_TELEMETRY_SIGNATURE_INVALID


def _verify_signature(public_key_pem: str, payload: bytes, signature: bytes) -> bool:
    """
    Pure Ed25519 check -- no I/O, no registry lookup. Given an
    already-resolved public key, verifies payload/signature match it.
    Only catches InvalidSignature (a genuine "doesn't match" result);
    a malformed PEM or wrong key type in the registry row is a data
    integrity bug in our own registry, not a normal verification
    outcome, and is deliberately left to raise its own error rather
    than being silently folded into "signature invalid".
    """
    public_key = serialization.load_pem_public_key(public_key_pem.encode())
    try:
        public_key.verify(signature, payload)
        return True
    except InvalidSignature:
        return False


async def verify_telemetry(session: AsyncSession, device_id: str, payload: bytes, signature: bytes) -> None:
    """
    Raises DeviceNotRegisteredError if device_id has no registry entry.
    Raises TelemetrySignatureInvalidError if it does, but the signature
    doesn't verify against the registered public key.
    Returns None (no exception) if the signature is valid.
    """
    public_key_pem = await fetch_device_public_key(session, device_id)
    if public_key_pem is None:
        raise DeviceNotRegisteredError(
            f"No device_registry entry for device_id={device_id!r}. "
            f"src/telemetry/models.py's device_registry table has no matching row."
        )

    if not _verify_signature(public_key_pem, payload, signature):
        raise TelemetrySignatureInvalidError(
            f"Signature verification failed for device_id={device_id!r} -- "
            f"payload does not match the registered public key."
        )
