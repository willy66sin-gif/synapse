"""
Admin-override request schema.

Per CLAUDE.md's Escalation Requirement, overriding a verdict is a
human action outside Core/Maestro's scope. OverrideRecord is the
validated, structured representation of that human action. On its
own it does nothing — it never flips or mutates an existing
adjudication result. Combined with src/supervisor/logic.py's
evaluate_override() and src/evidence/emitter.py's
emit_override_evidence(), it can only ever produce a new, additive,
independently signed evidence entry alongside the original
AdjudicationRecord for the same claim_id.
"""
from pydantic import BaseModel, ConfigDict, Field


class OverrideRecord(BaseModel):
    """
    Fail-closed like src/airlock/schemas.py's ClaimPayload:
    extra="forbid", and justification must be a real, non-empty
    string — an override with no stated reason is rejected at the
    schema boundary, before any authorization logic runs.
    """

    model_config = ConfigDict(extra="forbid")

    claim_id: str
    issuer_id: str
    justification: str = Field(min_length=1)
    timestamp: str
