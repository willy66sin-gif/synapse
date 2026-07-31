"""
Rule definitions & state checkers.

Zero-generation logic only. Every function here must be pure and
deterministic: same input -> same output, no external calls, no
probabilistic scoring, no LLM/NLP calls of any kind.

Ported from synapse_mdm.py's `adjudicate` method (Rule 1: Authority
Check, Rule 2: Physical Boundary & Zone Safety Check). The reference
harness reads its state from hardcoded ACTIVE_ZONES / AUTHORIZED_ISSUERS
dicts; here those are replaced by IssuerRecord / ZoneRecord data that
src/core/repository.py resolves from PostgreSQL / Redis. These
functions never do that resolution themselves — they only ever see
already-fetched records (or None), which is what keeps them pure and
testable without any database/cache side-effects.

verify_ptw_precondition (Rule 0: ePTW Precondition Check) needs no
externally-resolved record at all — the permit context travels inside
the claim itself (ClaimPayload.ptw_context), submitted directly by the
caller — so it stays pure with zero dependencies beyond the claim.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from src.airlock.schemas import ClaimPayload, WorkType


@dataclass(frozen=True)
class IssuerRecord:
    """Mirrors an AUTHORIZED_ISSUERS entry — sourced from PostgreSQL."""

    role: str
    clearance_level: int


@dataclass(frozen=True)
class ZoneRecord:
    """Mirrors an ACTIVE_ZONES entry — sourced from Redis."""

    hazard_level: str
    active_crane: bool


@dataclass(frozen=True)
class RuleOutcome:
    rule_id: str
    passed: bool
    reason: str


# Machine-readable failure codes for Verdict.reason_code (src/core/evaluator.py).
# Convention: R-<DOMAIN>-<NUMBER>, one per rule/failure class — not per individual
# sub-condition within a rule (e.g. every ePTW sub-check still reports R-PTW-01).
REASON_CODE_PTW_PRECONDITION = "R-PTW-01"
REASON_CODE_AUTHORITY_FAILURE = "R-AUTH-01"
REASON_CODE_ZONE_SAFETY_FAILURE = "R-ZONE-01"


def check_authority(claim: ClaimPayload, issuer_record: Optional[IssuerRecord]) -> RuleOutcome:
    """Rule 1: Authority Check (synapse_mdm.py `adjudicate`, Rule 1)."""
    if issuer_record is None:
        return RuleOutcome(
            rule_id="authority_check",
            passed=False,
            reason=f"Authority Failure: Issuer '{claim.issuer_id}' is unauthenticated.",
        )

    if claim.authority_level < issuer_record.clearance_level:
        return RuleOutcome(
            rule_id="authority_check",
            passed=False,
            reason=f"Authority Failure: Level {claim.authority_level} insufficient for role.",
        )

    return RuleOutcome(rule_id="authority_check", passed=True, reason="Authority Validated")


def check_zone_safety(claim: ClaimPayload, zone_record: Optional[ZoneRecord]) -> RuleOutcome:
    """Rule 2: Physical Boundary & Zone Safety Check (synapse_mdm.py `adjudicate`, Rule 2)."""
    if zone_record is None:
        return RuleOutcome(
            rule_id="zone_safety_check",
            passed=False,
            reason=f"Safety Violation: Zone '{claim.zone_id}' does not exist.",
        )

    if claim.action_type == "LIFT_OPERATION" and zone_record.hazard_level == "HIGH":
        return RuleOutcome(
            rule_id="zone_safety_check",
            passed=False,
            reason=f"Safety Violation: Heavy lift requested in high-hazard zone '{claim.zone_id}'.",
        )

    return RuleOutcome(rule_id="zone_safety_check", passed=True, reason="Zone Safety Validated")


HIGH_RISK_WORK_TYPES = frozenset(
    {WorkType.EXCAVATION, WorkType.LIFTING, WorkType.HOT_WORK, WorkType.CONFINED_SPACE}
)


def verify_ptw_precondition(claim: ClaimPayload) -> RuleOutcome:
    """
    Rule 0: ePTW Precondition Check. Gatekeeper — src/core/evaluator.py
    runs this before Rule 1 (authority) and Rule 2 (zone safety).

    NOMINAL_CIVIL work bypasses this gate transparently. Each of the
    four high-risk work types requires a PtwContext that is present,
    APPROVED, within its valid window, and matching on both zone_id
    and permit_type — or the claim fails closed.
    """
    if claim.work_type not in HIGH_RISK_WORK_TYPES:
        return RuleOutcome(
            rule_id="ptw_precondition_check",
            passed=True,
            reason=f"No permit required for work_type '{claim.work_type.value}'.",
        )

    ctx = claim.ptw_context

    if ctx is None:
        return RuleOutcome(
            rule_id="ptw_precondition_check",
            passed=False,
            reason=(
                f"FAIL_CLOSED_EPTW_PRECONDITION: No permit-to-work context provided "
                f"for high-risk work_type '{claim.work_type.value}'."
            ),
        )

    if ctx.status != "APPROVED":
        return RuleOutcome(
            rule_id="ptw_precondition_check",
            passed=False,
            reason=(
                f"FAIL_CLOSED_EPTW_PRECONDITION: Permit '{ctx.ptw_id}' status "
                f"'{ctx.status}' is not APPROVED."
            ),
        )

    now = datetime.now(timezone.utc)
    valid_from = datetime.fromisoformat(ctx.valid_from)
    valid_until = datetime.fromisoformat(ctx.valid_until)
    if now < valid_from or now > valid_until:
        return RuleOutcome(
            rule_id="ptw_precondition_check",
            passed=False,
            reason=(
                f"FAIL_CLOSED_EPTW_PRECONDITION: Permit '{ctx.ptw_id}' is outside its "
                f"valid window ({ctx.valid_from} to {ctx.valid_until})."
            ),
        )

    if ctx.zone_id != claim.zone_id:
        return RuleOutcome(
            rule_id="ptw_precondition_check",
            passed=False,
            reason=(
                f"FAIL_CLOSED_EPTW_PRECONDITION: Permit '{ctx.ptw_id}' zone "
                f"'{ctx.zone_id}' does not match claim zone '{claim.zone_id}'."
            ),
        )

    if ctx.permit_type != claim.work_type:
        return RuleOutcome(
            rule_id="ptw_precondition_check",
            passed=False,
            reason=(
                f"FAIL_CLOSED_EPTW_PRECONDITION: Permit '{ctx.ptw_id}' type "
                f"'{ctx.permit_type.value}' does not match claimed work_type "
                f"'{claim.work_type.value}'."
            ),
        )

    return RuleOutcome(
        rule_id="ptw_precondition_check",
        passed=True,
        reason=f"Permit '{ctx.ptw_id}' validated for '{claim.work_type.value}'.",
    )
