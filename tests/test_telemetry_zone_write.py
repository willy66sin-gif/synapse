"""
Verified-telemetry ZoneRecord write path tests (2026-08-27,
telemetry-ingestion-pathway build): src/telemetry/zone_write.py, plus
src/core/repository.py's now-sensor-aware fetch_zone_record().

No live Postgres/Redis -- same stub-session/fake-redis conventions
already used across this suite (tests/test_telemetry_trust.py's
_StubSession pattern, tests/test_airlock_maestro.py's _StubRedis
pattern), extended here since this path both reads and writes Redis
hashes and both reads and writes (add/commit) the database session.
"""
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.core.repository import fetch_zone_record
from src.telemetry.trust import (
    REASON_CODE_DEVICE_NOT_REGISTERED,
    REASON_CODE_TELEMETRY_SIGNATURE_INVALID,
    DeviceNotRegisteredError,
    TelemetrySignatureInvalidError,
)
from src.telemetry.zone_write import write_sensor_zone_state

DEVICE_ID = "DEV-CRANE-01"
ZONE_ID = "ZONE-01"
PAYLOAD = b'{"device_id": "DEV-CRANE-01", "zone_id": "ZONE-01", "field": "active_crane", "value": true}'


def _generate_keypair():
    private_key = Ed25519PrivateKey.generate()
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    return private_key, public_pem


class _StubSession:
    """
    Backs fetch_device_public_key() (execute().scalar_one_or_none(),
    same convention as tests/test_telemetry_trust.py's stub) and
    persist_sensor_zone_state_record() (add()/commit()) -- extended
    with the latter since this path also persists evidence, unlike the
    pure verify_telemetry() tests.
    """

    def __init__(self, public_key_pem):
        self._public_key_pem = public_key_pem
        self.added = []
        self.committed = False

    async def execute(self, _stmt):
        return self

    def scalar_one_or_none(self):
        return self._public_key_pem

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True


class _FakeRedis:
    """Minimal in-memory hash store -- hgetall/hset only, the two operations this path and fetch_zone_record() use."""

    def __init__(self):
        self._store: dict[str, dict[str, str]] = {}

    async def hgetall(self, key):
        return dict(self._store.get(key, {}))

    async def hset(self, key, field, value):
        self._store.setdefault(key, {})[field] = value


# --- sensor write success, readable back through fetch_zone_record() ---


@pytest.mark.asyncio
async def test_sensor_write_succeeds_and_is_readable_back_through_fetch_zone_record():
    private_key, public_pem = _generate_keypair()
    signature = private_key.sign(PAYLOAD)
    session = _StubSession(public_pem)
    redis_client = _FakeRedis()
    await redis_client.hset(f"zone:{ZONE_ID}", "hazard_level", "LOW")
    await redis_client.hset(f"zone:{ZONE_ID}", "active_crane", "false")

    evidence = await write_sensor_zone_state(
        session, redis_client, DEVICE_ID, ZONE_ID, "active_crane", True, PAYLOAD, signature
    )

    assert evidence["type"] == "SensorZoneStateRecord"
    assert evidence["source"] == "VERIFIED_TELEMETRY"
    assert evidence["device_id"] == DEVICE_ID
    assert evidence["zone_id"] == ZONE_ID
    assert evidence["field"] == "active_crane"
    assert evidence["value"] is True
    assert "sha256_signature" in evidence
    assert session.committed is True
    assert len(session.added) == 1

    zone_record = await fetch_zone_record(redis_client, ZONE_ID)
    assert zone_record.active_crane is True  # sensor value, not the human-declared "false"
    assert zone_record.hazard_level == "LOW"


# --- sensor-vs-human precedence, isolated from the write call itself ---


@pytest.mark.asyncio
async def test_sensor_value_takes_precedence_over_conflicting_human_declaration():
    redis_client = _FakeRedis()
    await redis_client.hset(f"zone:{ZONE_ID}", "hazard_level", "LOW")
    await redis_client.hset(f"zone:{ZONE_ID}", "active_crane", "false")
    await redis_client.hset(f"zone:{ZONE_ID}:sensor", "active_crane", "true")

    zone_record = await fetch_zone_record(redis_client, ZONE_ID)

    assert zone_record.active_crane is True
    assert zone_record.hazard_level == "LOW"  # untouched -- hazard_level has no sensor source


# --- fallback case: no registered sensor, existing seed-script path unaffected ---


@pytest.mark.asyncio
async def test_zone_with_no_registered_sensor_falls_back_to_human_declaration():
    redis_client = _FakeRedis()
    await redis_client.hset(f"zone:{ZONE_ID}", "hazard_level", "LOW")
    await redis_client.hset(f"zone:{ZONE_ID}", "active_crane", "false")

    zone_record = await fetch_zone_record(redis_client, ZONE_ID)

    assert zone_record.active_crane is False
    assert zone_record.hazard_level == "LOW"


# --- rejection paths (2026-08-27 telemetry-rejection-evidence addendum):
# no Redis write, but a distinct, persisted rejection evidence record ---


@pytest.mark.asyncio
async def test_unregistered_device_rejected_with_its_own_reason_code_and_no_redis_write():
    session = _StubSession(None)  # empty device_registry
    redis_client = _FakeRedis()
    await redis_client.hset(f"zone:{ZONE_ID}", "hazard_level", "LOW")
    await redis_client.hset(f"zone:{ZONE_ID}", "active_crane", "false")

    with pytest.raises(DeviceNotRegisteredError) as exc_info:
        await write_sensor_zone_state(
            session, redis_client, DEVICE_ID, ZONE_ID, "active_crane", True, PAYLOAD, b"signature"
        )

    assert exc_info.value.reason_code == REASON_CODE_DEVICE_NOT_REGISTERED == "R-DEV-01"
    assert (await redis_client.hgetall(f"zone:{ZONE_ID}:sensor")) == {}  # still no ZoneRecord write

    # but the rejection itself is now audited:
    assert session.committed is True
    assert len(session.added) == 1
    rejection = session.added[0].record
    assert rejection["type"] == "SensorZoneStateRejectionRecord"
    assert rejection["reason_code"] == "R-DEV-01"
    assert rejection["device_id"] == DEVICE_ID
    assert rejection["zone_id"] == ZONE_ID
    assert rejection["field"] == "active_crane"
    assert rejection["attempted_value"] is True
    assert rejection["source"] == "TELEMETRY_REJECTED"
    assert "sha256_signature" in rejection


@pytest.mark.asyncio
async def test_tampered_signature_rejected_with_its_own_reason_code_and_no_redis_write():
    _, public_pem = _generate_keypair()
    wrong_private_key, _ = _generate_keypair()
    signature = wrong_private_key.sign(PAYLOAD)
    session = _StubSession(public_pem)
    redis_client = _FakeRedis()
    await redis_client.hset(f"zone:{ZONE_ID}", "hazard_level", "LOW")
    await redis_client.hset(f"zone:{ZONE_ID}", "active_crane", "false")

    with pytest.raises(TelemetrySignatureInvalidError) as exc_info:
        await write_sensor_zone_state(
            session, redis_client, DEVICE_ID, ZONE_ID, "active_crane", True, PAYLOAD, signature
        )

    assert exc_info.value.reason_code == REASON_CODE_TELEMETRY_SIGNATURE_INVALID == "R-DEV-02"
    assert (await redis_client.hgetall(f"zone:{ZONE_ID}:sensor")) == {}  # still no ZoneRecord write

    assert session.committed is True
    assert len(session.added) == 1
    rejection = session.added[0].record
    assert rejection["type"] == "SensorZoneStateRejectionRecord"
    assert rejection["reason_code"] == "R-DEV-02"
    assert rejection["device_id"] == DEVICE_ID


def test_the_two_rejection_reason_codes_are_distinguishable_without_the_exception_type():
    """The whole point of threading reason_code into the persisted
    record: a reader of the audit trail can tell R-DEV-01 from R-DEV-02
    from the record alone, no exception object in hand."""
    assert REASON_CODE_DEVICE_NOT_REGISTERED != REASON_CODE_TELEMETRY_SIGNATURE_INVALID


@pytest.mark.asyncio
async def test_successful_write_creates_exactly_one_record_not_a_rejection_too():
    """Only one evidence record per outcome -- a success must not also
    leave a rejection-shaped record lying around."""
    private_key, public_pem = _generate_keypair()
    signature = private_key.sign(PAYLOAD)
    session = _StubSession(public_pem)
    redis_client = _FakeRedis()
    await redis_client.hset(f"zone:{ZONE_ID}", "hazard_level", "LOW")
    await redis_client.hset(f"zone:{ZONE_ID}", "active_crane", "false")

    await write_sensor_zone_state(
        session, redis_client, DEVICE_ID, ZONE_ID, "active_crane", True, PAYLOAD, signature
    )

    assert len(session.added) == 1
    assert session.added[0].record["type"] == "SensorZoneStateRecord"


@pytest.mark.asyncio
async def test_writing_a_non_sensor_eligible_field_is_rejected():
    """hazard_level has no sensor source named by this build -- refused
    outright, before even attempting device verification."""
    with pytest.raises(ValueError):
        await write_sensor_zone_state(None, None, DEVICE_ID, ZONE_ID, "hazard_level", True, PAYLOAD, b"sig")
