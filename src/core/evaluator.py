"""
Constitutional Adjudication Engine.

Stateless. Pure functions only — no database side-effects during
evaluation (per CLAUDE.md). Takes a validated claim, returns a
GO / NO-GO verdict plus the rule trace behind it.

Ported from synapse_mdm.py's `adjudicate` method. That reference
implementation fetches issuer/zone state itself, inline, from
hardcoded dicts. Here, resolution of IssuerRecord / ZoneRecord from
PostgreSQL / Redis happens one layer up (src/airlock/router.py, via
src/core/repository.py) — `adjudicate` only ever receives the
already-resolved records (or None), so it stays a pure function that
is testable without any database/cache side-effects, per CLAUDE.md's
Developer Directives.
"""
from dataclasses import asdict
from typing import Optional, TypedDict

from src.airlock.schemas import ClaimPayload
from src.core.rules import IssuerRecord, ZoneRecord, check_authority, check_zone_safety


class Verdict(TypedDict):
    claim_id: str
    decision: str  # "GO" | "NO_GO"
    reason: str
    rule_trace: list[dict]


def adjudicate(
    claim: ClaimPayload,
    issuer_record: Optional[IssuerRecord],
    zone_record: Optional[ZoneRecord],
) -> Verdict:
    """
    Evaluates a validated claim against Rule 1 (authority) then Rule 2
    (zone safety), short-circuiting NO_GO on the first failed rule —
    same control flow as synapse_mdm.py's `adjudicate`.
    """
    rule_trace: list[dict] = []

    authority_outcome = check_authority(claim, issuer_record)
    rule_trace.append(asdict(authority_outcome))
    if not authority_outcome.passed:
        return Verdict(
            claim_id=claim.claim_id,
            decision="NO_GO",
            reason=authority_outcome.reason,
            rule_trace=rule_trace,
        )

    zone_outcome = check_zone_safety(claim, zone_record)
    rule_trace.append(asdict(zone_outcome))
    if not zone_outcome.passed:
        return Verdict(
            claim_id=claim.claim_id,
            decision="NO_GO",
            reason=zone_outcome.reason,
            rule_trace=rule_trace,
        )

    return Verdict(
        claim_id=claim.claim_id,
        decision="GO",
        reason=f"Claim '{claim.claim_id}' cleared for execution in {claim.zone_id}.",
        rule_trace=rule_trace,
    )
