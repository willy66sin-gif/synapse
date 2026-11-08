"""
Rule definitions & state checkers.

Zero-generation logic only. Every function here must be pure and
deterministic: same input -> same output, no external calls, no
probabilistic scoring, no LLM/NLP calls of any kind. Nothing here
reads a clock (2026-11-08): Rule 0 (verify_ptw_precondition) judges
the permit's valid_from/valid_until window against an evaluation_time
the caller supplies, and Rule 3 judges against a caller-supplied
as_of -- so the same claim with the same times always gets the same
outcome. See src/core/evaluator.py's adjudicate() docstring.

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
from datetime import date, datetime, timedelta, timezone
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
    # tagged_asset_present (2026-09-10, second SENSOR_ELIGIBLE_ZONE_FIELDS
    # entry): RFID-type presence/proximity signal, carrier-agnostic by
    # design -- the physical carrier (human wearable, robot, robo-dog,
    # drone, fixed reader) is architecturally irrelevant, same as
    # active_crane not caring which specific crane. Defaulted to False,
    # not left required like hazard_level/active_crane, purely so the
    # several existing ZoneRecord(...) test fixtures across this repo
    # (tests/test_adjudication.py, tests/test_core_eptw.py,
    # tests/test_maestro_schemas.py) that predate this field keep
    # constructing valid records without being touched -- not a claim
    # that "no tag present" is the correct real-world default for every
    # zone, just the least-disruptive one for a field dataclasses.field
    # requires be declared after the two non-defaulted fields above it.
    # Field name is a proposal, not locked -- flagged back to Willy,
    # see this build's handoff report.
    tagged_asset_present: bool = False


# Recognised ZoneRecord.hazard_level values (2026-11-02, Willy-decided):
# exactly these two, case-sensitive. ZoneRecord.hazard_level stays a
# plain str -- src/core/repository.py's fetch_zone_record() returns None
# for it when the Redis hash has no such field -- so membership is
# checked in check_zone_safety(), below, for lift claims only.
VALID_HAZARD_LEVELS = frozenset({"LOW", "HIGH"})


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
#
# tagged_asset_present added 2026-09-10 as the second entry -- exactly
# the one-line addition this frozenset was designed for, no new
# precedence mechanism written. src/telemetry/zone_write.py's
# write_sensor_zone_state() and src/core/repository.py's
# fetch_zone_record()/_resolve_zone_field() needed zero code changes
# beyond this entry -- both were already generic over field name, per
# the 2026-09-09 investigate-only finding this build confirms rather
# than contradicts.
SENSOR_ELIGIBLE_ZONE_FIELDS = frozenset({"active_crane", "tagged_asset_present"})


def sensor_zone_redis_key(project_id: str, zone_id: str) -> str:
    """
    Redis key for verified-telemetry-sourced zone field values,
    distinct from the human-declared `zone:{project_id}:{zone_id}` hash
    src/core/repository.py's fetch_zone_record() already reads. Pure
    string formatting, no I/O -- both src/core/repository.py (reader)
    and src/telemetry/zone_write.py (writer) import this single
    definition so the two sides can never drift on the key format.

    project_id (2026-09-15, telemetry project-scoping follow-on --
    closes the gap the prior project-scoping pass flagged and left open:
    see CLAUDE.md's Changelog). Mirrors the human-declared zone key's own
    project_id namespacing exactly (`zone:{zone_id}` -> `zone:{project_id}:{zone_id}`,
    same prior pass) -- this key moves from `zone:{zone_id}:sensor` to
    `zone:{project_id}:{zone_id}:sensor` for the identical reason: a
    verified-telemetry write for one project's zone must not be
    readable as, or overwrite, another project's same-named zone.
    Cross-project telemetry isolation is enforced here, at the key
    level, by construction -- the same choice already made for the
    human-declared key -- not via a separate exception/reason-code
    check, since (unlike an issuer's authorized-projects list or a
    Certified Profile's own project_id) there is no independent "this
    zone belongs to project X" record anywhere in this codebase to
    validate a payload's declared project_id against; a zone's project
    membership IS its key, not a separate fact about it.
    """
    return f"zone:{project_id}:{zone_id}:sensor"


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
# R-LIFT-01 (Step 3, 2026-10-11): Rule 3, check_lift_panel_oem_consent(),
# below. Deliberately has no GATE_ADMISSIBLE_ROLES row: OEM consent is
# a precondition flag on the claim itself, not a role an issuer holds.
REASON_CODE_LIFT_PANEL_OEM_CONSENT = "R-LIFT-01"


@dataclass(frozen=True)
class RuleGrounding:
    """
    The external doctrine source a rule cites (Step 3, 2026-10-11).
    First of its kind here: no rule before check_lift_panel_oem_consent()
    cited anything.

    instrument_issuing_authority + instrument_reference_number are the
    link to src/regulatory/'s registry -- the same pair that table
    holds unique, findable with
    GET /regulatory/instruments?issuing_authority=...&reference_number=...
    The link is by value only. Core does no I/O, so a rule never looks
    its instrument up, never checks the row exists, and never reads the
    instrument's publish or effective date: a cited rule applies
    whether or not anyone has registered the instrument yet.

    Plain strings, not src/regulatory/ or src/doctrine/ types, so Core
    takes no dependency on either package for this.

    effective_date is the date the cited requirement takes effect, as
    an ISO date string -- or EFFECTIVE_DATE_UNCONFIRMED (below) while
    that date has not been confirmed. Held here, beside the rule, not
    read from the registry: the rule is hard-coded, so its commencement
    date is too. A string rather than a date so the whole grounding
    serializes into the hashed evidence record as-is.
    """

    instrument_issuing_authority: str
    instrument_reference_number: str
    provision: str
    basis: str
    effective_date: str


@dataclass(frozen=True)
class GroundedRuleOutcome(RuleOutcome):
    """
    A RuleOutcome that carries its RuleGrounding into rule_trace (and
    so into the hashed evidence record). A subclass rather than a new
    field on RuleOutcome itself, so the three pre-existing rules'
    rule_trace entries keep exactly the three keys they always had.

    evaluated_as_of records the adjudication time the rule was judged
    against (ISO string), or None if the caller supplied none -- so a
    reader of the evidence can see which side of effective_date the
    claim was placed on, and why.
    """

    grounding: RuleGrounding
    evaluated_as_of: Optional[str]


# Placeholder for a RuleGrounding.effective_date that has not been
# confirmed. NOT a date, on purpose: it cannot be compared, cannot be
# mistaken for a real commencement date, and cannot quietly put a claim
# on either side of one. While a rule's effective_date is this value,
# the rule cannot tell whether it applies to a claim, and fails closed
# for any claim it would otherwise have to judge -- see
# check_lift_panel_oem_consent().
EFFECTIVE_DATE_UNCONFIRMED = "UNCONFIRMED"

# Calendar-date boundary for effective_date: Singapore time (UTC+8, no
# daylight saving, so a fixed offset is exact). A requirement taking
# effect on a given date takes effect at 00:00 that day in Singapore,
# not at 00:00 UTC eight hours later.
EFFECTIVE_DATE_TIMEZONE = timezone(timedelta(hours=8))


# Grounding for Rule 3. Recorded as EXTERNAL_SOURCE: the requirement
# and its citation come from a primary document held by Willy -- the
# BCA advisory letter Our Ref: BCA/EMEG/GEN/2026/018, dated 30 Sep
# 2026, signed by the Group Director, EMEG -- supplied in conversation
# (2026-10-11). Neither the letter nor the Regulation it cites is in
# this repository, and nothing here verifies either; this constant
# records what the rule rests on, it does not attest to it.
#
# effective_date is EFFECTIVE_DATE_UNCONFIRMED -- PROVISIONAL, TO BE
# REPLACED. The date Regulation 38(1)(a) takes effect is being
# confirmed with BCA and has deliberately NOT been inferred from the
# letter's own date (30 Sep 2026): an issue date is not a commencement
# date. Replace it with the confirmed ISO date (e.g. "2026-MM-DD")
# once known; tests/test_lift_panel_rule.py's
# test_effective_date_is_still_an_unconfirmed_placeholder fails when
# that happens, so the change cannot land unnoticed.
LIFT_PANEL_OEM_CONSENT_GROUNDING = RuleGrounding(
    instrument_issuing_authority="BCA",
    instrument_reference_number="BCA/EMEG/GEN/2026/018",
    provision="Regulation 38(1)(a), Building Control (Fixed Installations) Regulations 2025",
    basis="EXTERNAL_SOURCE",
    effective_date=EFFECTIVE_DATE_UNCONFIRMED,
)

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

    # A claim counts as a lift (2026-11-03) when its action_type is
    # "LIFT_OPERATION" or its work_type is LIFTING -- so a LIFTING claim
    # carrying some other action label (e.g. "CRANE_RIGGING") gets both
    # lift checks below. Before this, only the action_type label counted.
    is_lift = claim.action_type == "LIFT_OPERATION" or claim.work_type == WorkType.LIFTING

    # Fail closed (2026-11-02): a lift cannot be cleared against a zone
    # whose hazard level is missing or not in VALID_HAZARD_LEVELS --
    # otherwise anything but the exact string "HIGH" would pass the
    # check below. Non-lift claims are unchanged.
    if is_lift and zone_record.hazard_level not in VALID_HAZARD_LEVELS:
        return RuleOutcome(
            rule_id="zone_safety_check",
            passed=False,
            reason=(
                f"Safety Violation: Hazard level for zone '{claim.zone_id}' is missing or unrecognised "
                f"({zone_record.hazard_level!r}); lift operation cannot be cleared."
            ),
        )

    if is_lift and zone_record.hazard_level == "HIGH":
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


def verify_ptw_precondition(
    claim: ClaimPayload, issuer_roles: list[AuthorityRoleType], evaluation_time: Optional[datetime]
) -> RuleOutcome:
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

    Lift label (2026-11-03): a claim whose work_type is not high-risk
    but whose action_type is "LIFT_OPERATION" is treated as LIFTING
    work for every check below -- it needs a LIFTING permit and an RTO
    issuer exactly as a declared LIFTING claim does. Each reason for
    such a claim names LIFTING and adds a clause saying the lift label
    caused the requirement. A claim whose work_type is already
    high-risk is unchanged: an EXCAVATION claim labelled
    LIFT_OPERATION still needs only its own EXCAVATION permit.

    evaluation_time (2026-11-08): the time the permit's
    valid_from/valid_until window is judged against, supplied by the
    caller -- this function reads no clock. Required as an argument; a
    naive (timezone-less) value raises, same as Rule 3's as_of. None is
    accepted only for a claim that never reaches the window check (no
    permit needed, permit missing, or not APPROVED); reaching the
    window check with None is a caller bug and raises rather than
    guessing a time.
    """
    if evaluation_time is not None and evaluation_time.tzinfo is None:
        raise ValueError("evaluation_time must be timezone-aware.")

    lift_label = claim.work_type not in HIGH_RISK_WORK_TYPES and claim.action_type == "LIFT_OPERATION"

    if claim.work_type not in HIGH_RISK_WORK_TYPES and not lift_label:
        return RuleOutcome(
            rule_id="ptw_precondition_check",
            passed=True,
            reason=f"No permit required for work_type '{claim.work_type.value}'.",
        )

    work_type = WorkType.LIFTING if lift_label else claim.work_type
    lift_label_clause = (
        f" Permit required because action_type 'LIFT_OPERATION' on work_type "
        f"'{claim.work_type.value}' is treated as LIFTING work."
        if lift_label
        else ""
    )

    ctx = claim.ptw_context

    if ctx is None:
        return RuleOutcome(
            rule_id="ptw_precondition_check",
            passed=False,
            reason=(
                f"FAIL_CLOSED_EPTW_PRECONDITION: No permit-to-work context provided "
                f"for high-risk work_type '{work_type.value}'.{lift_label_clause}"
            ),
        )

    if ctx.status != "APPROVED":
        return RuleOutcome(
            rule_id="ptw_precondition_check",
            passed=False,
            reason=(
                f"FAIL_CLOSED_EPTW_PRECONDITION: Permit '{ctx.ptw_id}' status "
                f"'{ctx.status}' is not APPROVED.{lift_label_clause}"
            ),
        )

    if evaluation_time is None:
        raise ValueError("evaluation_time is required to check the permit's valid_from/valid_until window.")
    valid_from = datetime.fromisoformat(ctx.valid_from)
    valid_until = datetime.fromisoformat(ctx.valid_until)
    if evaluation_time < valid_from or evaluation_time > valid_until:
        return RuleOutcome(
            rule_id="ptw_precondition_check",
            passed=False,
            reason=(
                f"FAIL_CLOSED_EPTW_PRECONDITION: Permit '{ctx.ptw_id}' is outside its "
                f"valid window ({ctx.valid_from} to {ctx.valid_until}).{lift_label_clause}"
            ),
        )

    if ctx.zone_id != claim.zone_id:
        return RuleOutcome(
            rule_id="ptw_precondition_check",
            passed=False,
            reason=(
                f"FAIL_CLOSED_EPTW_PRECONDITION: Permit '{ctx.ptw_id}' zone "
                f"'{ctx.zone_id}' does not match claim zone '{claim.zone_id}'.{lift_label_clause}"
            ),
        )

    if ctx.permit_type != work_type:
        return RuleOutcome(
            rule_id="ptw_precondition_check",
            passed=False,
            reason=(
                f"FAIL_CLOSED_EPTW_PRECONDITION: Permit '{ctx.ptw_id}' type "
                f"'{ctx.permit_type.value}' does not match claimed work_type "
                f"'{work_type.value}'.{lift_label_clause}"
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
                f"work_type '{work_type.value}'.{lift_label_clause}"
            ),
        )

    return RuleOutcome(
        rule_id="ptw_precondition_check",
        passed=True,
        reason=f"Permit '{ctx.ptw_id}' validated for '{work_type.value}'.{lift_label_clause}",
    )


def check_lift_panel_oem_consent(claim: ClaimPayload, as_of: Optional[datetime] = None) -> GroundedRuleOutcome:
    """
    Rule 3: Lift Control Panel OEM Consent Check (Step 3, 2026-10-11,
    Willy-authorized). src/core/evaluator.py runs this last, after
    Rule 2 (zone safety).

    A non-lift-related component installed in a lift control panel
    requires the OEM's technical assessment and consent. The claim
    is in scope when it declares lift_control_panel_work and
    non_lift_component_installed both True and oem_consent_obtained
    False. Every other combination passes without reference to any
    date: the requirement either does not apply (not panel work, or no
    non-lift component) or is declared met.

    Not retroactive. For an in-scope claim the requirement only binds
    from its effective date, so the outcome depends on as_of -- the
    time the claim was FIRST adjudicated, supplied by the caller from
    server-side time only (see src/core/evaluator.py's adjudicate()).
    as_of is deliberately not ClaimPayload.timestamp: that is an
    untyped, submitter-supplied string nothing verifies, and judging
    against it would let a claim be backdated past the rule. Nor is it
    read from the clock here: a stored claim re-evaluated on a later
    poll must be judged as of when it was first adjudicated, not as of
    the poll.

    For an in-scope claim, in order:
      - effective date unconfirmed (EFFECTIVE_DATE_UNCONFIRMED): the
        rule cannot tell whether it applies -- fails closed, and says
        so. This is the current state; see
        LIFT_PANEL_OEM_CONSENT_GROUNDING.
      - no as_of supplied: likewise cannot tell -- fails closed.
      - as_of before the effective date (Singapore calendar date):
        passes, "not yet in force".
      - as_of on or after it: fails, the requirement is not met.

    A naive (timezone-less) as_of is a caller bug, not a claim outcome:
    it raises rather than being guessed at.

    Hard-coded, like Rules 0-2 -- not a data-driven rule, and not a
    step toward the still-open multi-regulator ruleset item in
    CLAUDE.md. Reads only the three self-declared claim flags (see
    their comment on src/airlock/schemas.py's ClaimPayload): no
    issuer_roles parameter and no GATE_ADMISSIBLE_ROLES row, because
    OEM consent is a precondition on the claim, not a role. Keyed on
    the flags alone, not on work_type or action_type.

    Every outcome, pass or fail, carries LIFT_PANEL_OEM_CONSENT_GROUNDING
    -- see RuleGrounding for what that link does and does not do.
    """
    if as_of is not None and as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware.")

    grounding = LIFT_PANEL_OEM_CONSENT_GROUNDING
    evaluated_as_of = as_of.isoformat() if as_of is not None else None

    def _outcome(passed: bool, reason: str) -> GroundedRuleOutcome:
        return GroundedRuleOutcome(
            rule_id="lift_panel_oem_consent_check",
            passed=passed,
            reason=reason,
            grounding=grounding,
            evaluated_as_of=evaluated_as_of,
        )

    if not (claim.lift_control_panel_work and claim.non_lift_component_installed):
        return _outcome(
            True, "No OEM consent required: claim does not declare a non-lift component in a lift control panel."
        )

    if claim.oem_consent_obtained:
        return _outcome(True, "OEM consent declared for non-lift component in lift control panel.")

    citation = (
        f"{grounding.provision}; {grounding.instrument_issuing_authority} ref "
        f"{grounding.instrument_reference_number}"
    )

    if grounding.effective_date == EFFECTIVE_DATE_UNCONFIRMED:
        return _outcome(
            False,
            "FAIL_CLOSED_EFFECTIVE_DATE_UNCONFIRMED: Non-lift-related component in a lift control panel "
            "without OEM technical assessment and consent, and the effective date of the requirement "
            f"has not been confirmed, so whether it applies to this claim cannot be determined ({citation}).",
        )

    if as_of is None:
        return _outcome(
            False,
            "FAIL_CLOSED_NO_ADJUDICATION_TIME: Non-lift-related component in a lift control panel "
            "without OEM technical assessment and consent, and no adjudication time was supplied to "
            f"judge it against the requirement's effective date {grounding.effective_date} ({citation}).",
        )

    effective_date = date.fromisoformat(grounding.effective_date)
    if as_of.astimezone(EFFECTIVE_DATE_TIMEZONE).date() < effective_date:
        return _outcome(
            True,
            f"Requirement not yet in force when this claim was first adjudicated: effective "
            f"{grounding.effective_date}, adjudicated {evaluated_as_of} ({citation}).",
        )

    return _outcome(
        False,
        "Regulatory Requirement Not Met: Non-lift-related component in a lift control panel "
        f"without OEM technical assessment and consent ({citation}; in force from {grounding.effective_date}).",
    )
