"""
Airlock-stage profile_id requirement check (GO Freshness Phase 3a,
Part A, 2026-08-31, Willy-authorized).

Pure decision logic only -- no I/O, mirrors src/telemetry/trust.py's
own "fetch happens one layer up, this module only decides" discipline.
The caller (src/airlock/router.py) fetches profile_id's CertifiedProfile
(if any) via src/profiles/repository.py's fetch_certified_profile()
BEFORE calling check_profile_requirement() below, and passes the
already-resolved Optional[CertifiedProfile] in -- the same
"already-resolved record" pattern src/core/rules.py's issuer_record/
zone_record parameters follow.

Naming-convention check performed before writing this module: Airlock
itself has no existing precedent for a DB-lookup-backed, business-rule
rejection (its only rejection today is FastAPI/Pydantic's automatic
422 on ClaimPayload schema validation -- pure shape, no I/O, no reason
code, no evidence emission). The closest real precedent anywhere in
this codebase for "a fail-closed procedural rejection with its own
reason code and its own signed evidence record, distinct from Core's
adjudication trail" is src/telemetry/trust.py's DeviceNotRegisteredError
vs. TelemetrySignatureInvalidError split (R-DEV-01/R-DEV-02) --
ported here as ProfileIdMissingError/ProfileIdUnresolvableError
(R-PROFILE-01/R-PROFILE-02), same reasoning: two honestly
distinguishable failure classes (never supplied vs. supplied-but-wrong),
not a distinction invented for this pass -- classify_authority_failure()
(src/core/rules.py) and the R-DEV-01/R-DEV-02 split already established
that "provisioning gap" vs. "resolution/trust gap" is this codebase's
existing convention for splitting a reason code in two.

Grace-period design (locked, do not re-litigate -- see this pass's own
scoping doc): Settings.profile_id_enforcement_enabled defaults False.
While False, this function NEVER raises -- not even when a submitted
profile_id fails to resolve. This is a deliberate reading of the
locked decision, not an oversight: the whole point of a grace period
default-off is "nothing gets rejected because of this new dimension
until Willy flips the switch" (see src/config.py's Settings docstring
comment) -- an early adopter who supplies a wrong/typo'd profile_id
before enforcement is on gets that fact recorded in the audit trail
(ProfileCheckOutcome.reason says so explicitly), not silently ignored
and not rejected either. Only once profile_id_enforcement_enabled is
True does a missing or unresolvable profile_id raise.

ProfileCheckOutcome mirrors src/core/rules.py's RuleOutcome shape
exactly (rule_id/passed/reason) so src/airlock/router.py can append it
to a normally-adjudicated claim's Verdict.rule_trace the same way
Core's own three gates already populate that list -- reusing the
existing evidence shape for this note (per this pass's scoping doc)
rather than inventing a new top-level field on the evidence record.
`passed` here means exactly what it means on every other rule_trace
entry in this codebase: "did this cause a rejection" -- so it is
always True in every branch this function can return (as opposed to
raise) from, including the grace-period "unresolvable but not
enforced" case, since that case does NOT block the claim.
"""
from dataclasses import dataclass
from typing import Optional

from src.profiles.schemas import CertifiedProfile

REASON_CODE_PROFILE_MISSING = "R-PROFILE-01"
REASON_CODE_PROFILE_UNRESOLVABLE = "R-PROFILE-02"


class ProfileIdMissingError(LookupError):
    """profile_id_enforcement_enabled is True and the claim submitted no profile_id at all."""

    reason_code = REASON_CODE_PROFILE_MISSING


class ProfileIdUnresolvableError(LookupError):
    """profile_id_enforcement_enabled is True and the submitted profile_id has no matching CertifiedProfileRecord."""

    reason_code = REASON_CODE_PROFILE_UNRESOLVABLE


@dataclass(frozen=True)
class ProfileCheckOutcome:
    rule_id: str
    passed: bool
    reason: str


def check_profile_requirement(
    profile_id: Optional[str], profile: Optional[CertifiedProfile], enforcement_enabled: bool
) -> ProfileCheckOutcome:
    """
    Returns the profile_check rule_trace entry for a claim that is
    going to proceed to adjudicate() regardless. Raises
    ProfileIdMissingError/ProfileIdUnresolvableError instead -- never
    returning -- for the two cases where enforcement_enabled is True
    and the profile requirement genuinely fails; those two branches
    are handled entirely by src/airlock/router.py before adjudicate()
    is ever called, so a rejected claim never reaches this return path.
    """
    if profile_id is None:
        if enforcement_enabled:
            raise ProfileIdMissingError("No profile_id was submitted with this claim.")
        return ProfileCheckOutcome(
            rule_id="profile_check",
            passed=True,
            reason="Profile check skipped: no profile_id was submitted (profile_id_enforcement_enabled is False).",
        )

    if profile is None:
        if enforcement_enabled:
            raise ProfileIdUnresolvableError(
                f"profile_id '{profile_id}' does not match any registered CertifiedProfile."
            )
        return ProfileCheckOutcome(
            rule_id="profile_check",
            passed=True,
            reason=(
                f"Profile '{profile_id}' does not resolve to a registered CertifiedProfile -- not enforced "
                f"(profile_id_enforcement_enabled is False); claim proceeded regardless."
            ),
        )

    return ProfileCheckOutcome(
        rule_id="profile_check",
        passed=True,
        reason=f"Profile '{profile.profile_id}' (version {profile.version}) validated.",
    )
