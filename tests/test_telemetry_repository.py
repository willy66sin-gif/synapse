"""
Device registry lookup tests (src/telemetry/repository.py).

No live database -- same stub-session convention as
tests/test_core_repository.py (canned return regardless of statement
shape; there's only one lookup key here, device_id, so there's no
"wrong type" case to prove filtering against, unlike the identity
crosswalk's three-part key).
"""
import pytest

from src.telemetry.models import DeviceRegistryEntry
from src.telemetry.repository import fetch_device_public_key


class _StubResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _StubSession:
    def __init__(self, value):
        self._value = value

    async def execute(self, _stmt):
        return _StubResult(self._value)


@pytest.mark.asyncio
async def test_fetch_device_public_key_returns_key_for_a_registered_device():
    session = _StubSession("-----BEGIN PUBLIC KEY-----\nMCow...\n-----END PUBLIC KEY-----")

    result = await fetch_device_public_key(session, "DEV-TEST-01")

    assert result == "-----BEGIN PUBLIC KEY-----\nMCow...\n-----END PUBLIC KEY-----"


@pytest.mark.asyncio
async def test_fetch_device_public_key_returns_none_for_an_unregistered_device():
    session = _StubSession(None)

    result = await fetch_device_public_key(session, "DEV-UNKNOWN")

    assert result is None


def test_device_registry_entry_model_shape():
    """Schema shape check, no DB -- device_id/public_key/registered_since, nothing else."""
    entry = DeviceRegistryEntry(
        device_id="DEV-TEST-01",
        public_key="-----BEGIN PUBLIC KEY-----\nMCow...\n-----END PUBLIC KEY-----",
        registered_since="2026-08-10T00:00:00+00:00",
    )

    assert entry.device_id == "DEV-TEST-01"
    assert entry.registered_since == "2026-08-10T00:00:00+00:00"
