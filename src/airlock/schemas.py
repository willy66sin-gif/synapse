"""
Strict Pydantic v2 schemas for incoming claims.

Per CLAUDE.md fail-closed doctrine: no field is optional unless the
domain genuinely allows it. Anything that fails validation here
never reaches src/core/.
"""
from typing import Any

from pydantic import BaseModel, ConfigDict


class ClaimPayload(BaseModel):
    """
    Field set and types mirror synapse_mdm.py's `validate_schema`
    required_fields mapping exactly. `extra="forbid"` makes the
    Airlock reject anything outside that shape — including raw
    prose bodies — with HTTP 422, before it can reach src/core/.
    """

    model_config = ConfigDict(extra="forbid")

    claim_id: str
    timestamp: str
    issuer_id: str
    authority_level: int
    zone_id: str
    action_type: str
    payload_data: dict[str, Any]
