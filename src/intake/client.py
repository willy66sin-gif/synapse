"""
Thin HTTP transport for submitting an already-translated ClaimPayload
to the existing POST /airlock/claims endpoint.

Deliberately separate from ClaimSourceAdapter.translate()
(src/intake/adapters/base.py) -- per the approved design, POSTing is
identical plumbing for every current and future claim-source adapter,
so it does not belong inside the source-specific translate() method.

Goes over real HTTP to the real endpoint rather than importing
ClaimPayload's validators to construct/persist a claim directly --
Airlock's fail-closed schema gate stays the single source of truth for
every caller, this adapter included, not a second copy of it here.

No new endpoint, no retry/backoff policy, no business logic: this is
a call boundary, nothing more.
"""
from typing import Any

import httpx

from src.airlock.schemas import ClaimPayload


async def submit_claim(client: httpx.AsyncClient, claim: ClaimPayload, *, base_url: str) -> dict[str, Any]:
    """POST claim to {base_url}/airlock/claims and return the decoded JSON response body."""
    response = await client.post(f"{base_url}/airlock/claims", json=claim.model_dump(mode="json"))
    response.raise_for_status()
    return response.json()
