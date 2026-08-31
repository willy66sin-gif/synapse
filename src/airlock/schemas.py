"""
Strict Pydantic v2 schemas for incoming claims.

Per CLAUDE.md fail-closed doctrine: no field is optional unless the
domain genuinely allows it. Anything that fails validation here
never reaches src/core/.
"""
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


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
    # DEPRECATED (2026-08-28, Legacy authority_level/clearance_level
    # discovery pass): retained on this schema for backward API
    # compatibility only -- not read by adjudicate()/check_authority()
    # as of commit 2b26af0, which replaced the
    # claim.authority_level < issuer_record.clearance_level comparison
    # with GATE_ADMISSIBLE_ROLES/IssuerRole membership. Still
    # mandatory and still enforced at the schema boundary (this
    # model's extra="forbid" plus FastAPI's live /docs, /openapi.json)
    # -- removing it would be a breaking API contract change, not
    # internal cleanup. Three replacement options were presented for
    # this field (2026-08-06, CLAUDE.md changelog) and none has been
    # decided; do not remove this field until that decision is made.
    authority_level: int
    zone_id: str
    action_type: str
    payload_data: dict[str, Any]
    work_type: WorkType
    ptw_context: Optional[PtwContext] = None
    # Design-alteration self-declaration (2026-08-18): defaults to
    # False/None so every existing claim payload stays valid unedited.
    # Self-declared only, per explicit scope -- no detection logic, no
    # verification that the flag is honestly raised. Feeds
    # src/maestro/directory.py's resolve_authority() is_design_alteration
    # parameter (via evidence["input_payload"], same threading path
    # zone_id/action_type already use) to route to QP/QE.
    is_design_alteration: bool = False
    alteration_description: Optional[str] = None
    # GO Freshness Phase 3a (2026-08-31, Willy-authorized), design
    # decision #1: Optional[str] = None PERMANENTLY -- this must never
    # become a hard-required Pydantic field, even once enforcement is
    # switched on. "Required" is an application-level rule gated on
    # src/config.py's Settings.profile_id_enforcement_enabled, checked
    # at src/airlock/router.py (see src/airlock/profile_check.py), not
    # a schema-level constraint -- avoids a second breaking schema
    # migration once enforcement flips on. While the flag is off, a
    # claim that DOES supply profile_id still gets it resolved and
    # validated (see profile_check.py) -- early adopters aren't
    # penalized for sending it ahead of enforcement.
    profile_id: Optional[str] = None

    @model_validator(mode="after")
    def _alteration_description_matches_flag(self) -> "ClaimPayload":
        """
        Fail-closed at the schema boundary: alteration_description must
        be present when is_design_alteration is True (an alteration
        claim with no description is exactly the kind of malformed
        input this file's own doctrine rejects at 422, not best-effort
        parsed), and must be absent when False (a description with no
        flag is an equally inconsistent payload, not silently ignored).
        """
        if self.is_design_alteration and not self.alteration_description:
            raise ValueError("alteration_description is required when is_design_alteration is True.")
        if not self.is_design_alteration and self.alteration_description is not None:
            raise ValueError("alteration_description must be null when is_design_alteration is False.")
        return self
