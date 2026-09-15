"""
Airlock-stage cross-project reference check (project-scoping boundary,
2026-09-15, Ring-Fencing Concept Note, 26 Aug 2026 -- Willy-authorized
implementation).

Pure decision logic only -- no I/O, mirrors src/airlock/profile_check.py's
own "fetch happens one layer up, this module only decides" discipline.
The caller (src/airlock/router.py) fetches the claim's issuer's
authorized projects (src/core/repository.py's fetch_issuer_projects())
and, if the claim names one, its CertifiedProfile (src/profiles/repository.py's
fetch_certified_profile()) BEFORE calling check_project_scope() below,
and passes the already-resolved data in -- the same "already-resolved
record" pattern src/core/rules.py's issuer_record/zone_record parameters
and src/airlock/profile_check.py's check_profile_requirement() both
follow.

Structurally identical to check_profile_requirement(), per this pass's
own explicit instruction: same two-tier shape (a pure check that either
returns a passing ProjectScopeOutcome or raises a dedicated exception
carrying its own reason_code), same "append the outcome to
Verdict.rule_trace regardless" convention. Unlike profile_check.py,
there is no enforcement-flag grace period here -- project_id is a
required ClaimPayload field (not an optional one like profile_id), so
this check runs unconditionally on every claim, not gated behind a
Settings flag.

Scope, deliberately narrow (see src/core/repository.py's
fetch_zone_record() docstring for the zone side of this boundary, which
this module does NOT duplicate): this module checks the two references
that can be compared directly against a project_id already on file --
the submitting issuer's authorized-projects list (IssuerProject) and,
if named, the claim's CertifiedProfile.project_id. Zone-level project
isolation is enforced separately, at the Redis key level itself
(project_id is now part of the zone key), not duplicated here as a
third branch -- a cross-project zone_id reference already fails closed
by finding no zone record at all (R-ZONE-01, "zone does not exist"),
which is a pre-existing, sufficient fail-closed outcome for that case
and does not need a second, redundant R-PROJECT-01 path.

An unrecognized issuer (issuer_record is None -- no AuthorizedIssuer row
at all) is NOT this module's concern -- that is R-AUTH-01's job,
downstream in Core, and remains unchanged. This module only fires when
the issuer IS recognized, but is not authorized under the specific
project_id the claim declares -- a different failure mode from "we
don't know who this is."
"""
from dataclasses import dataclass
from typing import Optional

from src.core.rules import IssuerRecord
from src.profiles.schemas import CertifiedProfile

REASON_CODE_PROJECT_SCOPE_VIOLATION = "R-PROJECT-01"


class ProjectScopeViolationError(LookupError):
    """
    A recognized issuer or a resolved CertifiedProfile belongs to a
    different project_id than the one the claim declares.
    """

    reason_code = REASON_CODE_PROJECT_SCOPE_VIOLATION


@dataclass(frozen=True)
class ProjectScopeOutcome:
    rule_id: str
    passed: bool
    reason: str


def check_project_scope(
    claim_project_id: str,
    profile: Optional[CertifiedProfile],
    issuer_record: Optional[IssuerRecord],
    issuer_projects: list[str],
) -> ProjectScopeOutcome:
    """
    Returns the project_scope_check rule_trace entry for a claim that is
    going to proceed to adjudicate() regardless. Raises
    ProjectScopeViolationError instead -- never returning -- for the two
    cases where a genuine cross-project reference is detected;
    src/airlock/router.py handles that exception entirely before
    adjudicate() is ever called, so a rejected claim never reaches this
    return path, mirroring check_profile_requirement()'s own contract.

    profile is the already-resolved CertifiedProfile for claim.profile_id
    (or None if the claim named none, or named one that didn't resolve --
    that second case is src/airlock/profile_check.py's concern, handled
    earlier in the pipeline; by the time this function runs, a
    non-None profile here is trustworthy). issuer_record is None only
    when the issuer is genuinely unrecognized (R-AUTH-01's case, not
    this module's) -- see this module's own docstring for why that case
    is deliberately passed through here without raising.
    """
    if profile is not None and profile.project_id != claim_project_id:
        raise ProjectScopeViolationError(
            f"profile '{profile.profile_id}' belongs to project '{profile.project_id}', "
            f"not the claim's project '{claim_project_id}'."
        )

    if issuer_record is not None and claim_project_id not in issuer_projects:
        raise ProjectScopeViolationError(
            f"issuer is not authorized for project '{claim_project_id}'."
        )

    return ProjectScopeOutcome(
        rule_id="project_scope_check",
        passed=True,
        reason=f"Project scope validated for '{claim_project_id}'.",
    )
