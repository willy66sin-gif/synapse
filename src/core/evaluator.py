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

Rule 0 (verify_ptw_precondition, the ePTW gate) runs before Rule 1
(authority) and Rule 2 (zone safety) — a claim for high-risk work with
no valid permit is rejected before Core ever considers who submitted
it or where.
"""
from dataclasses import asdict
from typing import Optional, TypedDict

from src.airlock.schemas import ClaimPayload
from src.core.roles import AuthorityRoleType
from src.core.rules import (
    REASON_CODE_PTW_PRECONDITION,
    REASON_CODE_ZONE_SAFETY_FAILURE,
    IssuerRecord,
    ZoneRecord,
    check_authority,
    check_zone_safety,
    classify_authority_failure,
    verify_ptw_precondition,
)
from src.profiles.schemas import CertifiedProfile


class Verdict(TypedDict):
    claim_id: str
    decision: str  # "GO" | "NO_GO"
    reason: str
    rule_trace: list[dict]
    reason_code: Optional[str]  # machine-readable failure code; None unless a specific gate sets one


def adjudicate(
    claim: ClaimPayload,
    issuer_record: Optional[IssuerRecord],
    zone_record: Optional[ZoneRecord],
    issuer_roles: list[AuthorityRoleType],
    certified_profile: Optional[CertifiedProfile] = None,
) -> Verdict:
    """
    Evaluates a validated claim against Rule 0 (ePTW precondition),
    then Rule 1 (authority), then Rule 2 (zone safety), short-
    circuiting NO_GO on the first failed rule — same control flow as
    synapse_mdm.py's `adjudicate`, extended with the ePTW gate ahead
    of it.

    issuer_roles (2026-08-27, Authority Admissibility handoff; threaded
    into Rule 0 and Rule 2 as of 2026-08-28's R-ZONE-01/R-PTW-01
    Admissibility handoff): the issuer's already-fetched
    AuthorityRoleType list (src/core/repository.py's
    fetch_issuer_roles()), same "already-resolved record" discipline as
    issuer_record/zone_record — this function still does no I/O and no
    resolution itself. Now passed into all three rule calls below; see
    src/core/rules.py's GATE_ADMISSIBLE_ROLES for each gate's
    admissible-role row.

    certified_profile (2026-08-31, GO Freshness Phase 3a Part B; the
    already-resolved CertifiedProfile from src/profiles/repository.py's
    fetch_certified_profile(), same "already-resolved record"
    discipline as issuer_record/zone_record/issuer_roles above --
    Optional and defaulted to None so every existing caller of this
    function keeps working unchanged). Honest limitation, stated
    plainly rather than overstated: nothing in src/core/rules.py reads
    this parameter yet -- Rule 0/1/2 above are entirely unchanged by
    its presence, and passing None here (as every caller predating this
    pass still does) produces byte-identical behavior to before this
    pass. This wires the plumbing (fresh-fetched profile reaching
    Core, exactly like issuer/zone already do) without fabricating a
    rule for it to gate, per this pass's own explicit scope -- inventing
    a profile-parameter-driven rule was not authorized here. A future
    pass that gives a real rule a profile-scoped parameter to check
    (via src/core/profile_resolution.py's resolve_effective_parameters(),
    already built and tested but still uncalled from any production
    path) is what would actually change adjudication behavior; this one
    does not.
    """
    rule_trace: list[dict] = []

    ptw_outcome = verify_ptw_precondition(claim, issuer_roles)
    rule_trace.append(asdict(ptw_outcome))
    if not ptw_outcome.passed:
        return Verdict(
            claim_id=claim.claim_id,
            decision="NO_GO",
            reason=ptw_outcome.reason,
            rule_trace=rule_trace,
            reason_code=REASON_CODE_PTW_PRECONDITION,
        )

    authority_outcome = check_authority(claim, issuer_record, issuer_roles)
    rule_trace.append(asdict(authority_outcome))
    if not authority_outcome.passed:
        return Verdict(
            claim_id=claim.claim_id,
            decision="NO_GO",
            reason=authority_outcome.reason,
            rule_trace=rule_trace,
            # Second call to the same pure, deterministic classifier
            # check_authority() already used internally -- not a second
            # decision, same pattern as src/airlock/router.py's two calls
            # to resolve_authority(). See classify_authority_failure()'s
            # doc comment (src/core/rules.py) for why R-AUTH-01/02/03
            # split the way they do.
            reason_code=classify_authority_failure(claim, issuer_record, issuer_roles),
        )

    zone_outcome = check_zone_safety(claim, zone_record, issuer_roles)
    rule_trace.append(asdict(zone_outcome))
    if not zone_outcome.passed:
        return Verdict(
            claim_id=claim.claim_id,
            decision="NO_GO",
            reason=zone_outcome.reason,
            rule_trace=rule_trace,
            reason_code=REASON_CODE_ZONE_SAFETY_FAILURE,
        )

    return Verdict(
        claim_id=claim.claim_id,
        decision="GO",
        reason=f"Claim '{claim.claim_id}' cleared for execution in {claim.zone_id}.",
        rule_trace=rule_trace,
        reason_code=None,
    )
