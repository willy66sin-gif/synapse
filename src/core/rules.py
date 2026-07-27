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
"""
from dataclasses import dataclass
from typing import Optional

from src.airlock.schemas import ClaimPayload


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
