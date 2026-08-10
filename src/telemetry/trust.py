"""
Device telemetry trust verification.

Verifies that a telemetry payload genuinely came from the device that
claims to have sent it, unaltered in transit -- inbound only (per the
2026-08-10 design conversation, outbound instruction-authentication to
a physical actuator is a distinct, not-yet-relevant problem: Maestro's
only built output today is human notification, see
src/maestro/adapters/base.py).

No real caller wires into this today. Checked how src/core/rules.py's
ZoneRecord actually gets populated: nothing does, automatically --
src/core/repository.py's fetch_zone_record() only reads Redis, and the
only thing that ever writes a zone: key is scripts/seed_dev_data.py, a
manual, optional dev script. There is no telemetry ingestion pathway
anywhere in this codebase for this module to protect yet. This ships
as a real, testable, zero-data mechanism ahead of that pathway
existing -- same "build the container, not the content" discipline as
src/intake/models.py's IdentityCrosswalkEntry, one layer further out
(that registry at least had a real, if blocked, caller in EptwAdapter;
this one has none yet).

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


class DeviceNotRegisteredError(LookupError):
    """No device_registry entry for this device_id. Mechanism works, no data -- distinct from a verification failure."""


class TelemetrySignatureInvalidError(ValueError):
    """Device is registered, but the signature does not verify against its registered public key."""


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
