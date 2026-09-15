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
from src.telemetry.models import DeviceRegistryEntry, SensorZoneStateAuditEntry
from src.telemetry.trust import (
    REASON_CODE_DEVICE_NOT_REGISTERED,
    REASON_CODE_TELEMETRY_SIGNATURE_INVALID,
    DeviceNotRegisteredError,
    TelemetrySignatureInvalidError,
)
from src.telemetry.zone_write import write_sensor_zone_state

DEVICE_ID = "DEV-CRANE-01"
ZONE_ID = "ZONE-01"
# Project-scoping boundary (2026-09-15): both the human-declared zone
# hash key (zone:{project_id}:{zone_id}, see src/core/repository.py's
# fetch_zone_record()) and the verified-telemetry sensor hash key
# (zone:{project_id}:{zone_id}:sensor, see src/core/rules.py's
# sensor_zone_redis_key()) are namespaced by project_id.
PROJECT_ID = "PROJ-TEST-01"
PAYLOAD = b'{"device_id": "DEV-CRANE-01", "zone_id": "ZONE-01", "field": "active_crane", "value": true}'


def _generate_keypair():
    private_key = Ed25519PrivateKey.generate()
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    return private_key, public_pem


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _StubSession:
    """
    Entity-dispatch stub serving fetch_device_public_key()'s
    DeviceRegistryEntry query and (2026-09-15, device-reassignment
    anomaly detection) fetch_latest_sensor_zone_state_project_id()'s
    SensorZoneStateAuditEntry query off one session, plus
    persist_sensor_zone_state_record()'s add()/commit() -- same
    "dispatch by stmt.column_descriptions[0]['entity']" convention
    tests/test_airlock_maestro.py's/tests/test_airlock_profile.py's
    stub sessions already established, extended here since this path
    now issues two distinct SELECT shapes, not one.

    prior_project_id defaults to None (the common "first-ever write, or
    a stub not exercising this path" case) -- pass it explicitly to
    simulate a device with a real prior successful write on record.
    """

    def __init__(self, public_key_pem, prior_project_id=None):
        self._public_key_pem = public_key_pem
        self._prior_project_id = prior_project_id
        self.added = []
        self.committed = False

    async def execute(self, stmt):
        entity = stmt.column_descriptions[0]["entity"]
        if entity is DeviceRegistryEntry:
            return _Result(self._public_key_pem)
        if entity is SensorZoneStateAuditEntry:
            return _Result(self._prior_project_id)
        raise AssertionError(f"unexpected query in test_telemetry_zone_write.py stub: {stmt}")

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
    await redis_client.hset(f"zone:{PROJECT_ID}:{ZONE_ID}", "hazard_level", "LOW")
    await redis_client.hset(f"zone:{PROJECT_ID}:{ZONE_ID}", "active_crane", "false")

    evidence = await write_sensor_zone_state(
        session, redis_client, PROJECT_ID, DEVICE_ID, ZONE_ID, "active_crane", True, PAYLOAD, signature
    )

    assert evidence["type"] == "SensorZoneStateRecord"
    assert evidence["source"] == "VERIFIED_TELEMETRY"
    assert evidence["device_id"] == DEVICE_ID
    assert evidence["zone_id"] == ZONE_ID
    assert evidence["field"] == "active_crane"
    assert evidence["value"] is True
    # Device-reassignment anomaly detection (2026-09-15): this stub's
    # default prior_project_id=None models a device with no prior
    # successful write on record at all -- a genuine first-ever write,
    # not an anomaly, so project_changed_from must stay None.
    assert evidence["project_changed_from"] is None
    assert "sha256_signature" in evidence
    assert session.committed is True
    assert len(session.added) == 1

    zone_record = await fetch_zone_record(redis_client, PROJECT_ID, ZONE_ID)
    assert zone_record.active_crane is True  # sensor value, not the human-declared "false"
    assert zone_record.hazard_level == "LOW"


# --- device-reassignment anomaly detection (2026-09-15, telemetry
# project-scoping follow-on, part 2): detection only, write still
# succeeds either way -- see write_sensor_zone_state()'s own docstring ---


@pytest.mark.asyncio
async def test_consecutive_write_for_the_same_project_is_not_flagged():
    """A device's declared project_id matching its own most recently
    recorded successful write is the common, non-anomalous case --
    project_changed_from must stay None."""
    private_key, public_pem = _generate_keypair()
    signature = private_key.sign(PAYLOAD)
    session = _StubSession(public_pem, prior_project_id=PROJECT_ID)
    redis_client = _FakeRedis()
    await redis_client.hset(f"zone:{PROJECT_ID}:{ZONE_ID}", "hazard_level", "LOW")
    await redis_client.hset(f"zone:{PROJECT_ID}:{ZONE_ID}", "active_crane", "false")

    evidence = await write_sensor_zone_state(
        session, redis_client, PROJECT_ID, DEVICE_ID, ZONE_ID, "active_crane", True, PAYLOAD, signature
    )

    assert evidence["project_changed_from"] is None


@pytest.mark.asyncio
async def test_consecutive_write_for_a_different_project_is_flagged_but_still_succeeds(capsys):
    """A device's declared project_id differing from its own most
    recently recorded successful write is the anomaly this pass exists
    to surface -- flagged on the evidence record AND printed for
    immediate visibility, but the write itself must still succeed
    (detection, not enforcement -- see write_sensor_zone_state()'s own
    docstring)."""
    private_key, public_pem = _generate_keypair()
    signature = private_key.sign(PAYLOAD)
    session = _StubSession(public_pem, prior_project_id="PROJ-OLD")
    redis_client = _FakeRedis()
    await redis_client.hset(f"zone:{PROJECT_ID}:{ZONE_ID}", "hazard_level", "LOW")
    await redis_client.hset(f"zone:{PROJECT_ID}:{ZONE_ID}", "active_crane", "false")

    evidence = await write_sensor_zone_state(
        session, redis_client, PROJECT_ID, DEVICE_ID, ZONE_ID, "active_crane", True, PAYLOAD, signature
    )

    # The write still succeeded -- same assertions as the ordinary
    # success test above, unaffected by the flag.
    assert evidence["type"] == "SensorZoneStateRecord"
    assert session.committed is True
    assert len(session.added) == 1
    zone_record = await fetch_zone_record(redis_client, PROJECT_ID, ZONE_ID)
    assert zone_record.active_crane is True

    # The anomaly itself: queryable on the evidence record...
    assert evidence["project_changed_from"] == "PROJ-OLD"
    assert evidence["project_id"] == PROJECT_ID

    # ...and printed for immediate operational visibility.
    printed = capsys.readouterr().out
    assert "reassignment" in printed
    assert DEVICE_ID in printed
    assert "PROJ-OLD" in printed
    assert PROJECT_ID in printed


@pytest.mark.asyncio
async def test_first_ever_write_for_a_device_is_not_flagged():
    """No prior SensorZoneStateAuditEntry row at all for this device --
    there is nothing to compare against, so this must NOT be treated as
    an anomaly. Same scenario tests/test_sensor_write_succeeds_and_is_readable_back_through_fetch_zone_record
    already exercises implicitly (its stub's default prior_project_id
    is None); this test names the property explicitly."""
    private_key, public_pem = _generate_keypair()
    signature = private_key.sign(PAYLOAD)
    session = _StubSession(public_pem, prior_project_id=None)
    redis_client = _FakeRedis()
    await redis_client.hset(f"zone:{PROJECT_ID}:{ZONE_ID}", "hazard_level", "LOW")
    await redis_client.hset(f"zone:{PROJECT_ID}:{ZONE_ID}", "active_crane", "false")

    evidence = await write_sensor_zone_state(
        session, redis_client, PROJECT_ID, DEVICE_ID, ZONE_ID, "active_crane", True, PAYLOAD, signature
    )

    assert evidence["project_changed_from"] is None


# --- sensor-vs-human precedence, isolated from the write call itself ---


@pytest.mark.asyncio
async def test_sensor_value_takes_precedence_over_conflicting_human_declaration():
    redis_client = _FakeRedis()
    await redis_client.hset(f"zone:{PROJECT_ID}:{ZONE_ID}", "hazard_level", "LOW")
    await redis_client.hset(f"zone:{PROJECT_ID}:{ZONE_ID}", "active_crane", "false")
    await redis_client.hset(f"zone:{PROJECT_ID}:{ZONE_ID}:sensor", "active_crane", "true")

    zone_record = await fetch_zone_record(redis_client, PROJECT_ID, ZONE_ID)

    assert zone_record.active_crane is True
    assert zone_record.hazard_level == "LOW"  # untouched -- hazard_level has no sensor source


# --- fallback case: no registered sensor, existing seed-script path unaffected ---


@pytest.mark.asyncio
async def test_zone_with_no_registered_sensor_falls_back_to_human_declaration():
    redis_client = _FakeRedis()
    await redis_client.hset(f"zone:{PROJECT_ID}:{ZONE_ID}", "hazard_level", "LOW")
    await redis_client.hset(f"zone:{PROJECT_ID}:{ZONE_ID}", "active_crane", "false")

    zone_record = await fetch_zone_record(redis_client, PROJECT_ID, ZONE_ID)

    assert zone_record.active_crane is False
    assert zone_record.hazard_level == "LOW"


# --- rejection paths (2026-08-27 telemetry-rejection-evidence addendum):
# no Redis write, but a distinct, persisted rejection evidence record ---


@pytest.mark.asyncio
async def test_unregistered_device_rejected_with_its_own_reason_code_and_no_redis_write():
    session = _StubSession(None)  # empty device_registry
    redis_client = _FakeRedis()
    await redis_client.hset(f"zone:{PROJECT_ID}:{ZONE_ID}", "hazard_level", "LOW")
    await redis_client.hset(f"zone:{PROJECT_ID}:{ZONE_ID}", "active_crane", "false")

    with pytest.raises(DeviceNotRegisteredError) as exc_info:
        await write_sensor_zone_state(
            session, redis_client, PROJECT_ID, DEVICE_ID, ZONE_ID, "active_crane", True, PAYLOAD, b"signature"
        )

    assert exc_info.value.reason_code == REASON_CODE_DEVICE_NOT_REGISTERED == "R-DEV-01"
    assert (await redis_client.hgetall(f"zone:{PROJECT_ID}:{ZONE_ID}:sensor")) == {}  # still no ZoneRecord write

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
    await redis_client.hset(f"zone:{PROJECT_ID}:{ZONE_ID}", "hazard_level", "LOW")
    await redis_client.hset(f"zone:{PROJECT_ID}:{ZONE_ID}", "active_crane", "false")

    with pytest.raises(TelemetrySignatureInvalidError) as exc_info:
        await write_sensor_zone_state(
            session, redis_client, PROJECT_ID, DEVICE_ID, ZONE_ID, "active_crane", True, PAYLOAD, signature
        )

    assert exc_info.value.reason_code == REASON_CODE_TELEMETRY_SIGNATURE_INVALID == "R-DEV-02"
    assert (await redis_client.hgetall(f"zone:{PROJECT_ID}:{ZONE_ID}:sensor")) == {}  # still no ZoneRecord write

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
    await redis_client.hset(f"zone:{PROJECT_ID}:{ZONE_ID}", "hazard_level", "LOW")
    await redis_client.hset(f"zone:{PROJECT_ID}:{ZONE_ID}", "active_crane", "false")

    await write_sensor_zone_state(
        session, redis_client, PROJECT_ID, DEVICE_ID, ZONE_ID, "active_crane", True, PAYLOAD, signature
    )

    assert len(session.added) == 1
    assert session.added[0].record["type"] == "SensorZoneStateRecord"


@pytest.mark.asyncio
async def test_writing_a_non_sensor_eligible_field_is_rejected():
    """hazard_level has no sensor source named by this build -- refused
    outright, before even attempting device verification."""
    with pytest.raises(ValueError):
        await write_sensor_zone_state(None, None, PROJECT_ID, DEVICE_ID, ZONE_ID, "hazard_level", True, PAYLOAD, b"sig")


# --- tagged_asset_present (2026-09-10, second SENSOR_ELIGIBLE_ZONE_FIELDS
# entry -- RFID-type presence/proximity signal, carrier-agnostic by
# design). Mirrors the active_crane coverage above exactly, on a
# second, distinct field, to confirm write_sensor_zone_state() and
# fetch_zone_record()/_resolve_zone_field() needed zero changes beyond
# the SENSOR_ELIGIBLE_ZONE_FIELDS entry itself -- both were already
# generic over field name/value, per the 2026-09-09 investigate-only
# finding this build confirms rather than contradicts. Field name is a
# proposal, not locked -- see src/core/rules.py's ZoneRecord docstring
# and this build's handoff report. ---

RFID_DEVICE_ID = "DEV-RFID-01"
RFID_PAYLOAD = b'{"device_id": "DEV-RFID-01", "zone_id": "ZONE-01", "field": "tagged_asset_present", "value": true}'


@pytest.mark.asyncio
async def test_tagged_asset_sensor_write_succeeds_and_is_readable_back_through_fetch_zone_record():
    private_key, public_pem = _generate_keypair()
    signature = private_key.sign(RFID_PAYLOAD)
    session = _StubSession(public_pem)
    redis_client = _FakeRedis()
    await redis_client.hset(f"zone:{PROJECT_ID}:{ZONE_ID}", "hazard_level", "LOW")
    await redis_client.hset(f"zone:{PROJECT_ID}:{ZONE_ID}", "active_crane", "false")
    await redis_client.hset(f"zone:{PROJECT_ID}:{ZONE_ID}", "tagged_asset_present", "false")

    evidence = await write_sensor_zone_state(
        session, redis_client, PROJECT_ID, RFID_DEVICE_ID, ZONE_ID, "tagged_asset_present", True, RFID_PAYLOAD, signature
    )

    assert evidence["type"] == "SensorZoneStateRecord"
    assert evidence["source"] == "VERIFIED_TELEMETRY"
    assert evidence["device_id"] == RFID_DEVICE_ID
    assert evidence["zone_id"] == ZONE_ID
    assert evidence["field"] == "tagged_asset_present"
    assert evidence["value"] is True
    assert "sha256_signature" in evidence
    assert session.committed is True
    assert len(session.added) == 1

    zone_record = await fetch_zone_record(redis_client, PROJECT_ID, ZONE_ID)
    assert zone_record.tagged_asset_present is True  # sensor value, not the human-declared "false"
    assert zone_record.active_crane is False  # untouched -- independent sensor-eligible field
    assert zone_record.hazard_level == "LOW"


@pytest.mark.asyncio
async def test_tagged_asset_sensor_value_takes_precedence_over_conflicting_human_declaration():
    redis_client = _FakeRedis()
    await redis_client.hset(f"zone:{PROJECT_ID}:{ZONE_ID}", "hazard_level", "LOW")
    await redis_client.hset(f"zone:{PROJECT_ID}:{ZONE_ID}", "active_crane", "false")
    await redis_client.hset(f"zone:{PROJECT_ID}:{ZONE_ID}", "tagged_asset_present", "false")
    await redis_client.hset(f"zone:{PROJECT_ID}:{ZONE_ID}:sensor", "tagged_asset_present", "true")

    zone_record = await fetch_zone_record(redis_client, PROJECT_ID, ZONE_ID)

    assert zone_record.tagged_asset_present is True
    assert zone_record.active_crane is False  # untouched -- no sensor value written for this field
    assert zone_record.hazard_level == "LOW"


@pytest.mark.asyncio
async def test_zone_with_no_registered_tagged_asset_sensor_falls_back_to_human_declaration():
    redis_client = _FakeRedis()
    await redis_client.hset(f"zone:{PROJECT_ID}:{ZONE_ID}", "hazard_level", "LOW")
    await redis_client.hset(f"zone:{PROJECT_ID}:{ZONE_ID}", "active_crane", "false")
    await redis_client.hset(f"zone:{PROJECT_ID}:{ZONE_ID}", "tagged_asset_present", "true")

    zone_record = await fetch_zone_record(redis_client, PROJECT_ID, ZONE_ID)

    assert zone_record.tagged_asset_present is True
    assert zone_record.hazard_level == "LOW"


@pytest.mark.asyncio
async def test_zone_with_no_tagged_asset_field_declared_at_all_defaults_to_false():
    """A zone predating this field (human-declared hash has no
    tagged_asset_present key at all) resolves to False, not an error --
    _resolve_zone_field()'s human_data.get(field) returns None, and
    None == "true" is False. Confirms the dataclass default (False) and
    the read-path fallback agree for a zone that simply never declared
    this field, not just one that declared it False explicitly."""
    redis_client = _FakeRedis()
    await redis_client.hset(f"zone:{PROJECT_ID}:{ZONE_ID}", "hazard_level", "LOW")
    await redis_client.hset(f"zone:{PROJECT_ID}:{ZONE_ID}", "active_crane", "false")

    zone_record = await fetch_zone_record(redis_client, PROJECT_ID, ZONE_ID)

    assert zone_record.tagged_asset_present is False


@pytest.mark.asyncio
async def test_unregistered_device_rejected_for_tagged_asset_field_with_its_own_reason_code_and_no_redis_write():
    session = _StubSession(None)  # empty device_registry
    redis_client = _FakeRedis()
    await redis_client.hset(f"zone:{PROJECT_ID}:{ZONE_ID}", "hazard_level", "LOW")
    await redis_client.hset(f"zone:{PROJECT_ID}:{ZONE_ID}", "active_crane", "false")

    with pytest.raises(DeviceNotRegisteredError) as exc_info:
        await write_sensor_zone_state(
            session, redis_client, PROJECT_ID, RFID_DEVICE_ID, ZONE_ID, "tagged_asset_present", True, RFID_PAYLOAD, b"signature"
        )

    assert exc_info.value.reason_code == REASON_CODE_DEVICE_NOT_REGISTERED == "R-DEV-01"
    assert (await redis_client.hgetall(f"zone:{PROJECT_ID}:{ZONE_ID}:sensor")) == {}  # still no ZoneRecord write

    rejection = session.added[0].record
    assert rejection["type"] == "SensorZoneStateRejectionRecord"
    assert rejection["reason_code"] == "R-DEV-01"
    assert rejection["field"] == "tagged_asset_present"


@pytest.mark.asyncio
async def test_tampered_signature_rejected_for_tagged_asset_field_with_its_own_reason_code_and_no_redis_write():
    _, public_pem = _generate_keypair()
    wrong_private_key, _ = _generate_keypair()
    signature = wrong_private_key.sign(RFID_PAYLOAD)
    session = _StubSession(public_pem)
    redis_client = _FakeRedis()
    await redis_client.hset(f"zone:{PROJECT_ID}:{ZONE_ID}", "hazard_level", "LOW")
    await redis_client.hset(f"zone:{PROJECT_ID}:{ZONE_ID}", "active_crane", "false")

    with pytest.raises(TelemetrySignatureInvalidError) as exc_info:
        await write_sensor_zone_state(
            session, redis_client, PROJECT_ID, RFID_DEVICE_ID, ZONE_ID, "tagged_asset_present", True, RFID_PAYLOAD, signature
        )

    assert exc_info.value.reason_code == REASON_CODE_TELEMETRY_SIGNATURE_INVALID == "R-DEV-02"
    assert (await redis_client.hgetall(f"zone:{PROJECT_ID}:{ZONE_ID}:sensor")) == {}  # still no ZoneRecord write


def test_zone_record_tagged_asset_present_defaults_to_false_when_unspecified():
    """Dataclass default, not a read-path fallback: confirms existing
    ZoneRecord(hazard_level=..., active_crane=...) fixtures elsewhere in
    this suite (tests/test_adjudication.py, tests/test_core_eptw.py,
    tests/test_maestro_schemas.py) still construct valid records
    unchanged, without needing to name this field."""
    from src.core.rules import ZoneRecord

    record = ZoneRecord(hazard_level="LOW", active_crane=False)
    assert record.tagged_asset_present is False
