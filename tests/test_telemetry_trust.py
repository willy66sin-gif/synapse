"""
Device telemetry trust verification tests (src/telemetry/trust.py).

Uses real Ed25519 keypairs (via the `cryptography` package, already a
declared dependency, previously unused anywhere in this codebase) to
generate genuine signatures and genuine mismatches -- not mocked
crypto. No live database: a stub session stands in for the device
registry, same convention as tests/test_telemetry_repository.py.
"""
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.telemetry.trust import (
    DeviceNotRegisteredError,
    TelemetrySignatureInvalidError,
    _verify_signature,
    verify_telemetry,
)


def _generate_keypair():
    private_key = Ed25519PrivateKey.generate()
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    return private_key, public_pem


class _StubSession:
    """Stands in for a device_registry lookup returning a fixed public key (or None for an empty registry)."""

    def __init__(self, public_key_pem):
        self._public_key_pem = public_key_pem

    async def execute(self, _stmt):
        return self

    def scalar_one_or_none(self):
        return self._public_key_pem


# --- pure signature check ---


def test_verify_signature_accepts_a_genuinely_valid_signature():
    private_key, public_pem = _generate_keypair()
    payload = b'{"device_id": "DEV-TEST-01", "reading": "ok"}'
    signature = private_key.sign(payload)

    assert _verify_signature(public_pem, payload, signature) is True


def test_verify_signature_rejects_a_signature_from_a_different_key():
    _, public_pem = _generate_keypair()
    wrong_private_key, _ = _generate_keypair()
    payload = b'{"device_id": "DEV-TEST-01", "reading": "ok"}'
    signature = wrong_private_key.sign(payload)

    assert _verify_signature(public_pem, payload, signature) is False


def test_verify_signature_rejects_a_tampered_payload():
    private_key, public_pem = _generate_keypair()
    original_payload = b'{"reading": "ok"}'
    signature = private_key.sign(original_payload)
    tampered_payload = b'{"reading": "tampered"}'

    assert _verify_signature(public_pem, tampered_payload, signature) is False


# --- verify_telemetry(): registry lookup + signature check, two distinct failure modes ---


@pytest.mark.asyncio
async def test_verify_telemetry_succeeds_for_a_registered_device_with_a_valid_signature():
    private_key, public_pem = _generate_keypair()
    payload = b'{"device_id": "DEV-TEST-01", "reading": "ok"}'
    signature = private_key.sign(payload)
    session = _StubSession(public_pem)

    result = await verify_telemetry(session, "DEV-TEST-01", payload, signature)

    assert result is None


@pytest.mark.asyncio
async def test_verify_telemetry_raises_device_not_registered_for_an_empty_registry():
    """The real, current state of device_registry -- ships with zero rows."""
    session = _StubSession(None)

    with pytest.raises(DeviceNotRegisteredError, match="DEV-UNKNOWN"):
        await verify_telemetry(session, "DEV-UNKNOWN", b"payload", b"signature")


@pytest.mark.asyncio
async def test_verify_telemetry_raises_invalid_signature_for_a_registered_device_signed_by_the_wrong_key():
    _, public_pem = _generate_keypair()
    wrong_private_key, _ = _generate_keypair()
    payload = b'{"device_id": "DEV-TEST-01", "reading": "ok"}'
    signature = wrong_private_key.sign(payload)
    session = _StubSession(public_pem)

    with pytest.raises(TelemetrySignatureInvalidError, match="DEV-TEST-01"):
        await verify_telemetry(session, "DEV-TEST-01", payload, signature)


@pytest.mark.asyncio
async def test_verify_telemetry_raises_invalid_signature_for_a_tampered_payload():
    private_key, public_pem = _generate_keypair()
    original_payload = b'{"reading": "ok"}'
    signature = private_key.sign(original_payload)
    tampered_payload = b'{"reading": "tampered"}'
    session = _StubSession(public_pem)

    with pytest.raises(TelemetrySignatureInvalidError):
        await verify_telemetry(session, "DEV-TEST-01", tampered_payload, signature)


def test_device_not_registered_and_signature_invalid_are_distinct_exception_types():
    """The two failure modes must not be collapsed into one type -- see trust.py's module docstring."""
    assert not issubclass(DeviceNotRegisteredError, TelemetrySignatureInvalidError)
    assert not issubclass(TelemetrySignatureInvalidError, DeviceNotRegisteredError)
