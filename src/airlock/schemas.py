"""
Strict Pydantic v2 schemas for incoming claims.

Per CLAUDE.md fail-closed doctrine: no field is optional unless the
domain genuinely allows it. Anything that fails validation here
never reaches src/core/.
"""
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, field_validator


class WorkType(str, Enum):
    """
    Work classification driving the ePTW precondition gate
    (src/core/rules.py's verify_ptw_precondition). NOMINAL_CIVIL is
    the only category that bypasses the gate transparently; the other
    four each require an approved, in-date, zone-and-type-matching
    PtwContext, or the claim fails closed.
    """

    NOMINAL_CIVIL = "NOMINAL_CIVIL"
    EXCAVATION = "EXCAVATION"
    LIFTING = "LIFTING"
    HOT_WORK = "HOT_WORK"
    CONFINED_SPACE = "CONFINED_SPACE"


class PtwContext(BaseModel):
    """
    Permit-to-work context, optional on ClaimPayload — its *absence*
    is itself one of the fail-closed conditions verify_ptw_precondition
    checks for, but only for high-risk work_type values; NOMINAL_CIVIL
    claims never need one.

    permit_type reuses WorkType rather than a bare str, so the "type
    mismatch" check is a same-typed equality comparison against a
    closed set of known categories, not a string compared against
    unvalidated free text.
    """

    model_config = ConfigDict(extra="forbid")

    ptw_id: str
    status: str
    valid_from: str
    valid_until: str
    permit_type: WorkType
    zone_id: str
    issuer_id: str

    @field_validator("valid_from", "valid_until")
    @classmethod
    def _must_be_aware_iso_datetime(cls, value: str) -> str:
        """
        Fail-closed at the schema boundary, not deep inside a pure
        rule check: reject unparseable or timezone-naive timestamps
        here (422) rather than letting verify_ptw_precondition crash
        on an ambiguous comparison against "now".
        """
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError("must include a UTC offset (e.g. a trailing 'Z' or '+00:00')")
        return value


class ClaimPayload(BaseModel):
    """
    Field set and types mirror synapse_mdm.py's `validate_schema`
    required_fields mapping exactly. `extra="forbid"` makes the
    Airlock reject anything outside that shape — including raw
    prose bodies — with HTTP 422, before it can reach src/core/.

    work_type and ptw_context were added for the ePTW precondition
    check. work_type is a top-level, explicitly-typed field rather
    than folded into payload_data, matching the existing precedent
    set by action_type: fields rule logic actually branches on live
    at the top level; payload_data is reserved for opaque,
    rule-irrelevant operational detail (e.g. truck_id, weight_tons).
    work_type is mandatory, not defaulted — per this file's own
    fail-closed doctrine, the domain doesn't genuinely allow omitting
    work classification, since that's the very thing that decides
    whether PTW gating applies at all.
    """

    model_config = ConfigDict(extra="forbid")

    claim_id: str
    timestamp: str
    issuer_id: str
    authority_level: int
    zone_id: str
    action_type: str
    payload_data: dict[str, Any]
    work_type: WorkType
    ptw_context: Optional[PtwContext] = None
