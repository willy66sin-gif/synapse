"""
Fail-closed schema boundary tests.

Must cover: valid claim accepted, missing required field rejected,
unstructured/prose body rejected, malformed JSON rejected.

The HTTP-level tests override the Core's DB/Redis dependencies with
no-op fakes so the fail-closed behavior can be asserted without a live
PostgreSQL/Redis — Pydantic validation rejects malformed bodies before
adjudication would ever run.
"""
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.airlock.schemas import ClaimPayload
from src.core.repository import get_db_session, get_redis_client
from src.main import app

VALID_CLAIM = {
    "claim_id": "CLM-101",
    "timestamp": "2026-07-27T10:00:00Z",
    "issuer_id": "USR-SUP-01",
    "authority_level": 3,
    "zone_id": "ZONE-01",
    "action_type": "MATERIAL_ENTRY",
    "payload_data": {"truck_id": "SG1234A", "weight_tons": 12.5},
    "work_type": "NOMINAL_CIVIL",
}


async def _fake_db_session():
    yield None


async def _fake_redis_client():
    yield None


@pytest.fixture
def client():
    app.dependency_overrides[get_db_session] = _fake_db_session
    app.dependency_overrides[get_redis_client] = _fake_redis_client
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_valid_claim_accepted():
    claim = ClaimPayload(**VALID_CLAIM)

    assert claim.claim_id == "CLM-101"
    assert claim.payload_data == {"truck_id": "SG1234A", "weight_tons": 12.5}


def test_missing_required_field_rejected():
    payload = dict(VALID_CLAIM)
    del payload["zone_id"]

    with pytest.raises(ValidationError):
        ClaimPayload(**payload)


def test_wrong_field_type_rejected():
    payload = dict(VALID_CLAIM)
    payload["authority_level"] = "three"  # str instead of required int

    with pytest.raises(ValidationError):
        ClaimPayload(**payload)


def test_unstructured_prose_rejected(client):
    """Airlock fail-closed doctrine: raw prose must never reach Core."""
    response = client.post(
        "/airlock/claims",
        json={"raw_prose": "Hey can we move the truck to Zone 1?"},
    )

    assert response.status_code == 422


def test_malformed_json_rejected(client):
    response = client.post(
        "/airlock/claims",
        content=b"{not valid json",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 422
