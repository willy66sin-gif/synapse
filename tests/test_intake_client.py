"""
src/intake/client.py transport tests. Mocked transport only -- no live
HTTP calls, per the testing plan (client.py is tested separately from
translate(), which has no HTTP dependency at all).
"""
import httpx
import pytest

from src.airlock.schemas import ClaimPayload, WorkType
from src.intake.client import submit_claim

CLAIM = ClaimPayload(
    claim_id="CLM-EPTW-001",
    timestamp="2026-08-09T10:15:00+00:00",
    issuer_id="USR-SUP-01",
    authority_level=3,
    zone_id="ZONE-01",
    action_type="HOT_CUTTING",
    payload_data={"crew_size": 4},
    work_type=WorkType.NOMINAL_CIVIL,
)


@pytest.mark.asyncio
async def test_submit_claim_posts_to_airlock_claims_and_returns_json():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("http://airlock.local/airlock/claims")
        assert request.method == "POST"
        return httpx.Response(200, json={"decision": "GO"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await submit_claim(client, CLAIM, base_url="http://airlock.local")

    assert result == {"decision": "GO"}


@pytest.mark.asyncio
async def test_submit_claim_raises_on_http_error_status():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": "Schema Error"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await submit_claim(client, CLAIM, base_url="http://airlock.local")
