"""
POST /telemetry/zone-state tests (2026-09-09, telemetry-http-endpoint
build).

Reuses tests/test_telemetry_zone_write.py's own fixtures directly
(_StubSession, _FakeRedis, DEVICE_ID/ZONE_ID/PAYLOAD, _generate_keypair)
rather than duplicating that setup — this file only adds the HTTP
layer on top: request/response shape, status codes, and the
base64 payload/signature encoding a real HTTP body needs. Dependency
overrides follow the same TestClient + app.dependency_overrides
pattern as tests/test_airlock_maestro.py and
tests/test_supervisor_router.py (get_db_session / get_redis_client).
"""
import base64

import pytest
from fastapi.testclient import TestClient

from src.core.repository import get_db_session, get_redis_client
from src.main import app
from tests.test_telemetry_zone_write import (
    DEVICE_ID,
    PAYLOAD,
    PROJECT_ID,
    RFID_DEVICE_ID,
    RFID_PAYLOAD,
    ZONE_ID,
    _FakeRedis,
    _StubSession,
    _generate_keypair,
)


def _client_with_stubs(session, redis_client):
    async def _override_db_session():
        yield session

    async def _override_redis_client():
        yield redis_client

    app.dependency_overrides[get_db_session] = _override_db_session
    app.dependency_overrides[get_redis_client] = _override_redis_client
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


async def _seeded_redis() -> _FakeRedis:
    redis_client = _FakeRedis()
    await redis_client.hset(f"zone:{PROJECT_ID}:{ZONE_ID}", "hazard_level", "LOW")
    await redis_client.hset(f"zone:{PROJECT_ID}:{ZONE_ID}", "active_crane", "false")
    return redis_client


def _body(
    project_id=PROJECT_ID,
    device_id=DEVICE_ID,
    zone_id=ZONE_ID,
    field="active_crane",
    value=True,
    payload=PAYLOAD,
    signature=b"",
):
    return {
        "project_id": project_id,
        "device_id": device_id,
        "zone_id": zone_id,
        "field": field,
        "value": value,
        "payload": base64.b64encode(payload).decode(),
        "signature": base64.b64encode(signature).decode(),
    }


@pytest.mark.asyncio
async def test_registered_device_success_returns_200_with_sensor_zone_state_evidence():
    private_key, public_pem = _generate_keypair()
    signature = private_key.sign(PAYLOAD)
    session = _StubSession(public_pem)
    redis_client = await _seeded_redis()

    with _client_with_stubs(session, redis_client) as client:
        response = client.post("/telemetry/zone-state", json=_body(signature=signature))

    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "SensorZoneStateRecord"
    assert body["source"] == "VERIFIED_TELEMETRY"
    assert body["project_id"] == PROJECT_ID
    assert body["device_id"] == DEVICE_ID
    assert body["zone_id"] == ZONE_ID
    assert body["field"] == "active_crane"
    assert body["value"] is True
    assert "sha256_signature" in body


@pytest.mark.asyncio
async def test_registered_device_success_for_tagged_asset_present_field_returns_200():
    """Mirrors the active_crane success test above on the second
    SENSOR_ELIGIBLE_ZONE_FIELDS entry -- confirms the HTTP layer needed
    no field-specific changes either, same as the router/schema being
    written generically over `field` from the start."""
    private_key, public_pem = _generate_keypair()
    signature = private_key.sign(RFID_PAYLOAD)
    session = _StubSession(public_pem)
    redis_client = await _seeded_redis()

    with _client_with_stubs(session, redis_client) as client:
        response = client.post(
            "/telemetry/zone-state",
            json=_body(
                device_id=RFID_DEVICE_ID,
                field="tagged_asset_present",
                payload=RFID_PAYLOAD,
                signature=signature,
            ),
        )

    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "SensorZoneStateRecord"
    assert body["device_id"] == RFID_DEVICE_ID
    assert body["field"] == "tagged_asset_present"
    assert body["value"] is True


@pytest.mark.asyncio
async def test_unregistered_device_returns_422_with_r_dev_01():
    session = _StubSession(None)  # empty device registry
    redis_client = await _seeded_redis()

    with _client_with_stubs(session, redis_client) as client:
        response = client.post("/telemetry/zone-state", json=_body(signature=b"irrelevant"))

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["reason_code"] == "R-DEV-01"


@pytest.mark.asyncio
async def test_bad_signature_returns_422_with_r_dev_02():
    _, public_pem = _generate_keypair()
    wrong_private_key, _ = _generate_keypair()
    tampered_signature = wrong_private_key.sign(PAYLOAD)
    session = _StubSession(public_pem)
    redis_client = await _seeded_redis()

    with _client_with_stubs(session, redis_client) as client:
        response = client.post("/telemetry/zone-state", json=_body(signature=tampered_signature))

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["reason_code"] == "R-DEV-02"


def test_malformed_payload_missing_field_returns_422():
    session = _StubSession(None)
    redis_client = _FakeRedis()

    with _client_with_stubs(session, redis_client) as client:
        response = client.post("/telemetry/zone-state", json={"device_id": DEVICE_ID})

    assert response.status_code == 422


def test_extra_field_is_rejected_same_fail_closed_discipline_as_claim_payload():
    session = _StubSession(None)
    redis_client = _FakeRedis()

    with _client_with_stubs(session, redis_client) as client:
        response = client.post(
            "/telemetry/zone-state", json=_body(signature=b"x") | {"unexpected_field": "nope"}
        )

    assert response.status_code == 422


def test_non_base64_payload_returns_422():
    session = _StubSession(None)
    redis_client = _FakeRedis()

    body = _body(signature=b"x")
    body["payload"] = "not-valid-base64!!"

    with _client_with_stubs(session, redis_client) as client:
        response = client.post("/telemetry/zone-state", json=body)

    assert response.status_code == 422


# --- Project-scoping boundary (2026-09-15, telemetry follow-on) ---


def test_missing_project_id_is_rejected_with_a_plain_structural_422():
    """project_id is a required TelemetryZoneStatePayload field -- a
    request omitting it entirely never reaches submit_zone_state()'s
    body at all, rejected by FastAPI/Pydantic before any application
    code runs. Same structural-rejection class as
    tests/test_airlock_project.py's identical claim-level test."""
    session = _StubSession(None)
    redis_client = _FakeRedis()

    body = _body(signature=b"x")
    del body["project_id"]

    with _client_with_stubs(session, redis_client) as client:
        response = client.post("/telemetry/zone-state", json=body)

    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)


@pytest.mark.asyncio
async def test_write_for_one_project_does_not_leak_into_another_projects_zone_state():
    """Cross-project isolation is enforced by construction, at the Redis
    key level (src/core/rules.py's sensor_zone_redis_key(project_id,
    zone_id)) -- not via a separate exception/reason-code check (see
    that function's own docstring for why no independent "zone's actual
    project" record exists to validate a mismatch against). This is the
    positive proof of that property: a verified write declaring
    project_id="PROJ-OTHER" for the same zone_id must not be visible
    when reading PROJECT_ID's zone state back."""
    private_key, public_pem = _generate_keypair()
    signature = private_key.sign(PAYLOAD)
    session = _StubSession(public_pem)
    redis_client = await _seeded_redis()  # seeds PROJECT_ID's zone, active_crane=false

    with _client_with_stubs(session, redis_client) as client:
        response = client.post(
            "/telemetry/zone-state", json=_body(project_id="PROJ-OTHER", signature=signature)
        )

    assert response.status_code == 200
    assert response.json()["project_id"] == "PROJ-OTHER"

    from src.core.repository import fetch_zone_record

    own_project_zone = await fetch_zone_record(redis_client, PROJECT_ID, ZONE_ID)
    assert own_project_zone.active_crane is False  # unaffected by the other project's write

    other_project_zone = await fetch_zone_record(redis_client, "PROJ-OTHER", ZONE_ID)
    assert other_project_zone is None  # no human-declared zone exists there at all
