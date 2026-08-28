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
caller. It does take issuer_roles (2026-08-28, R-ZONE-01/R-PTW-01
Admissibility handoff), but that's the same already-fetched-list
parameter check_authority() already takes, not a resolution this
function performs itself — it stays pure and does zero I/O.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from src.airlock.schemas import ClaimPayload, WorkType
from src.core.roles import AuthorityRoleType


@dataclass(frozen=True)
class IssuerRecord:
    """Mirrors an AUTHORIZED_ISSUERS entry — sourced from PostgreSQL."""

    role: str
    # DEPRECATED (2026-08-28, Legacy authority_level/clearance_level
    # discovery pass): unread by check_authority()/classify_authority_
    # failure() since commit 2b26af0 -- superseded by
    # GATE_ADMISSIBLE_ROLES/IssuerRole membership (see
    # classify_authority_failure()'s own docstring, below). Kept on
    # this dataclass, not removed, for symmetry with
    # ClaimPayload.authority_level (src/airlock/schemas.py) -- both
    # went dead for the same reason on the same commit; actual removal
    # is a separate, future decision, not made here.
    clearance_level: int


@dataclass(frozen=True)
class ZoneRecord:
    """Mirrors an ACTIVE_ZONES entry — sourced from Redis."""

    hazard_level: str
    active_crane: bool


# Sensor/human dual-input precedence (2026-08-27, telemetry-ingestion-
# pathway build): a ZoneRecord field named here may be written either
# by a human declaration (scripts/seed_dev_data.py, the only writer
# before this build) or by verified telemetry
# (src/telemetry/zone_write.py's write_sensor_zone_state(), which
# calls src/telemetry/trust.py's verify_telemetry() before writing).
# When both exist for the same zone, the verified sensor value is
# authoritative and the human-declared value is fallback-only --
# src/core/repository.py's fetch_zone_record() is what applies this at
# read time. A frozenset, not a single hardcoded "active_crane" check,
# so a future dual-input field is a one-line addition here, not a new
# precedence mechanism. hazard_level is deliberately absent: no sensor
# source for it has been named by any decision to date.
SENSOR_ELIGIBLE_ZONE_FIELDS = frozenset({"active_crane"})


def sensor_zone_redis_key(zone_id: str) -> str:
    """
    Redis key for verified-telemetry-sourced zone field values,
    distinct from the human-declared `zone:{zone_id}` hash
    src/core/repository.py's fetch_zone_record() already reads. Pure
    string formatting, no I/O -- both src/core/repository.py (reader)
    and src/telemetry/zone_write.py (writer) import this single
    definition so the two sides can never drift on the key format.
    """
    return f"zone:{zone_id}:sensor"


@dataclass(frozen=True)
class RuleOutcome:
    rule_id: str
    passed: bool
    reason: str


# Machine-readable failure codes for Verdict.reason_code (src/core/evaluator.py).
# Convention: R-<DOMAIN>-<NUMBER>, one per rule/failure class — not per individual
# sub-condition within a rule (e.g. every ePTW sub-check still reports R-PTW-01).
#
# R-AUTH-01/02/03 (2026-08-06, R-AUTH-01 disambiguation) are a deliberate,
# documented exception to "one per rule": Rule 1 (authority_check) now
# produces three, not one, because two of its three failure branches
# turned out to be honestly distinguishable from claim.work_type alone --
# see classify_authority_failure()'s doc comment for exactly what is and
# isn't distinguishable and why.
REASON_CODE_PTW_PRECONDITION = "R-PTW-01"
REASON_CODE_AUTHORITY_FAILURE = "R-AUTH-01"
REASON_CODE_AUTHORITY_FAILURE_HIGH_RISK = "R-AUTH-02"
REASON_CODE_AUTHORITY_FAILURE_NOMINAL = "R-AUTH-03"
REASON_CODE_ZONE_SAFETY_FAILURE = "R-ZONE-01"

# Also used by verify_ptw_precondition, below -- moved up so both it and
# classify_authority_failure() can reference the same, single frozenset.
HIGH_RISK_WORK_TYPES = frozenset(
    {WorkType.EXCAVATION, WorkType.LIFTING, WorkType.HOT_WORK, WorkType.CONFINED_SPACE}
)


# Per-gate admissible-role mapping (2026-08-27, Authority Admissibility
# handoff -- locked decision, not reopened here). Static, code-defined,
# mirrors src/maestro/directory.py's DIRECTORY_MAP precedent exactly:
# no database table, no migration, a plain dict read by pure functions.
#
# All four rows are now wired (2026-08-28, R-ZONE-01/R-PTW-01
# Admissibility handoff): check_zone_safety() and verify_ptw_precondition()
# (below) each take issuer_roles and consult their own row via the same
# GATE_ADMISSIBLE_ROLES[REASON_CODE].intersection(issuer_roles) pattern
# classify_authority_failure() established for the authority_check rows.
# The mapping itself is unchanged from the prior pass -- only the two
# previously-inert rows are now read.
# Design alteration (QP+QE) is deliberately absent from this table: it
# is already implemented, but as escalation routing
# (src/maestro/directory.py's resolve_authority()), not a Core gate --
# out of scope for this table, which is about admissibility, not
# escalation ownership.
GATE_ADMISSIBLE_ROLES: dict[str, frozenset[AuthorityRoleType]] = {
    REASON_CODE_ZONE_SAFETY_FAILURE: frozenset({AuthorityRoleType.SA}),  # NOT WIRED -- see note above
    REASON_CODE_PTW_PRECONDITION: frozenset({AuthorityRoleType.RTO}),  # NOT WIRED -- see note above
    REASON_CODE_AUTHORITY_FAILURE_HIGH_RISK: frozenset({AuthorityRoleType.RTO}),
    REASON_CODE_AUTHORITY_FAILURE_NOMINAL: frozenset({AuthorityRoleType.RTO}),
}


def classify_authority_failure(
    claim: ClaimPayload, issuer_record: Optional[IssuerRecord], issuer_roles: list[AuthorityRoleType]
) -> Optional[str]:
    """
    Single source of truth for which Rule 1 (authority_check) failure
    mode applies, if any -- returns None if the claim would pass (not
    a failure to classify). check_authority() below calls this to
    decide which RuleOutcome.reason text to build; src/core/evaluator.py's
    adjudicate() calls it a second time to set Verdict.reason_code --
    a deliberate duplicate call to the same pure, deterministic
    function, not a second decision, same pattern already established
    by src/airlock/router.py's two calls to resolve_authority(). This
    keeps the two entirely in lockstep: there is exactly one place
    this branching logic is written, not two copies that could drift.

    issuer_roles (2026-08-27, Authority Admissibility handoff):
    replaces the prior claim.authority_level < issuer_record.
    clearance_level comparison with GATE_ADMISSIBLE_ROLES membership --
    an already-fetched list (src/core/repository.py's
    fetch_issuer_roles(), same "pure function only sees already-
    fetched records" discipline as issuer_record/zone_record), checked
    via set intersection so an issuer holding several roles (the real-
    world precedent that motivated IssuerRole's join-table shape) is
    handled naturally: any one admissible role present is enough,
    holding additional roles is never a problem. claim.authority_level
    and issuer_record.clearance_level are deliberately left unread here
    (not deleted from their schemas -- see CLAUDE.md's Open Items on
    the undefined clearance_level scale this replaces).

    What's honestly distinguishable today, and what isn't:
    - Unauthenticated issuer (issuer_record is None) stays a single,
      undifferentiated code (R-AUTH-01). This is deliberate, not an
      oversight: "we don't recognize this issuer" has nothing to do
      with what they're claiming -- an unknown issuer is unknown
      whether the claim is excavation or material entry. Splitting
      this by claim.work_type would invent a distinction the failure
      itself doesn't actually have.
    - Missing the admissible role (GATE_ADMISSIBLE_ROLES membership)
      DOES have an honest signal available: claim.work_type, via the
      same HIGH_RISK_WORK_TYPES distinction verify_ptw_precondition
      already uses elsewhere in this file -- not a new taxonomy
      invented for this purpose, the one real categorization already
      established in this codebase. R-AUTH-02 for high-risk work_type
      (excavation, lifting, hot work, confined space -- the same
      permit-requiring categories Rule 0 gates on), R-AUTH-03 for
      nominal work. Both currently require the same admissible role
      (RTO) -- the split is about which reason_code is reported, not
      about two different admissible-role sets.
    - claim.zone_id and claim.action_type are NOT used: zone hazard
      level isn't available here at all (check_authority never
      receives zone_record -- that's check_zone_safety's job, Rule 2,
      which runs after this one), and action_type has no established
      vocabulary anywhere in this codebase (a plain free string) to
      honestly categorize against -- using it would mean inventing
      string-matching heuristics on unstructured text, not
      distinguishing something the check can actually tell today.
    """
    if issuer_record is None:
        return REASON_CODE_AUTHORITY_FAILURE

    is_high_risk = claim.work_type in HIGH_RISK_WORK_TYPES
    reason_code = REASON_CODE_AUTHORITY_FAILURE_HIGH_RISK if is_high_risk else REASON_CODE_AUTHORITY_FAILURE_NOMINAL
    admissible_roles = GATE_ADMISSIBLE_ROLES[reason_code]

    if not admissible_roles.intersection(issuer_roles):
        return reason_code
    return None


def check_authority(
    claim: ClaimPayload, issuer_record: Optional[IssuerRecord], issuer_roles: list[AuthorityRoleType]
) -> RuleOutcome:
    """Rule 1: Authority Check (synapse_mdm.py `adjudicate`, Rule 1)."""
    failure = classify_authority_failure(claim, issuer_record, issuer_roles)

    if failure == REASON_CODE_AUTHORITY_FAILURE:
        return RuleOutcome(
            rule_id="authority_check",
            passed=False,
            reason=f"Authority Failure: Issuer '{claim.issuer_id}' is unauthenticated.",
        )

    if failure is not None:
        return RuleOutcome(
            rule_id="authority_check",
            passed=False,
            reason=(
                f"Authority Failure: Issuer '{claim.issuer_id}' does not hold an admissible "
                f"role ({sorted(role.value for role in GATE_ADMISSIBLE_ROLES[failure])}) for this claim."
            ),
        )

    return RuleOutcome(rule_id="authority_check", passed=True, reason="Authority Validated")


def check_zone_safety(
    claim: ClaimPayload, zone_record: Optional[ZoneRecord], issuer_roles: list[AuthorityRoleType]
) -> RuleOutcome:
    """
    Rule 2: Physical Boundary & Zone Safety Check (synapse_mdm.py
    `adjudicate`, Rule 2).

    issuer_roles (2026-08-28, R-ZONE-01/R-PTW-01 Admissibility
    handoff): checked via GATE_ADMISSIBLE_ROLES[REASON_CODE_ZONE_SAFETY_FAILURE]
    (SA), same intersection pattern classify_authority_failure()
    established. Ordered after the existence check and the
    hazard-specific lift-operation check, not before them: an
    inadmissible-issuer failure and a physical hazard failure are both
    genuine zone-safety violations reported under the same R-ZONE-01
    code, and putting the pre-existing hazard check first keeps its
    failure reason unchanged for issuers who fail both ways at once --
    least disruption to what this rule already reported, per the
    R-ZONE-01/R-PTW-01 handoff's "reuse, least new surface area"
    direction.
    """
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

    admissible_roles = GATE_ADMISSIBLE_ROLES[REASON_CODE_ZONE_SAFETY_FAILURE]
    if not admissible_roles.intersection(issuer_roles):
        return RuleOutcome(
            rule_id="zone_safety_check",
            passed=False,
            reason=(
                f"Safety Violation: Issuer does not hold an admissible role "
                f"({sorted(role.value for role in admissible_roles)}) for zone '{claim.zone_id}'."
            ),
        )

    return RuleOutcome(rule_id="zone_safety_check", passed=True, reason="Zone Safety Validated")


def verify_ptw_precondition(claim: ClaimPayload, issuer_roles: list[AuthorityRoleType]) -> RuleOutcome:
    """
    Rule 0: ePTW Precondition Check. Gatekeeper — src/core/evaluator.py
    runs this before Rule 1 (authority) and Rule 2 (zone safety).

    NOMINAL_CIVIL work bypasses this gate transparently. Each of the
    four high-risk work types requires a PtwContext that is present,
    APPROVED, within its valid window, and matching on both zone_id
    and permit_type — or the claim fails closed.

    issuer_roles (2026-08-28, R-ZONE-01/R-PTW-01 Admissibility
    handoff): checked last, after every ctx-content check above,
    via GATE_ADMISSIBLE_ROLES[REASON_CODE_PTW_PRECONDITION] (RTO), same
    intersection pattern classify_authority_failure() established.
    Deliberately last, not first: this gate's existing ctx-content
    reasons (missing/PENDING/EXPIRED/REVOKED/out-of-window/mismatched
    permit) stay reported exactly as before for a claim that fails both
    ways at once -- an inadmissible issuer is only reported once the
    permit itself has already checked out, same "least disruption to
    what already reports" choice made in check_zone_safety().
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

    admissible_roles = GATE_ADMISSIBLE_ROLES[REASON_CODE_PTW_PRECONDITION]
    if not admissible_roles.intersection(issuer_roles):
        return RuleOutcome(
            rule_id="ptw_precondition_check",
            passed=False,
            reason=(
                f"FAIL_CLOSED_EPTW_PRECONDITION: Issuer does not hold an admissible role "
                f"({sorted(role.value for role in admissible_roles)}) for high-risk "
                f"work_type '{claim.work_type.value}'."
            ),
        )

    return RuleOutcome(
        rule_id="ptw_precondition_check",
        passed=True,
        reason=f"Permit '{ctx.ptw_id}' validated for '{claim.work_type.value}'.",
    )
