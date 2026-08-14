"""
AuthorityRoleType — canonical location (2026-08-06).

Moved here from src/maestro/directory.py, where it originated as part
of the Supervisor Override Retirement work (5 Aug 2026). It belongs in
src/core/ now that src/core/models.py's IssuerRole also needs it:
Core must not depend on Maestro (a downstream delivery layer -- "Core
decides, Maestro delivers", per CLAUDE.md's System Principle), so the
type has to live somewhere both packages can depend on without
inverting that direction. src/maestro/directory.py now imports it from
here instead of defining it -- re-exported there for backward
compatibility, no behavior change.

Zero dependencies (plain stdlib enum) so both src/core/models.py
(SQLAlchemy) and src/maestro/directory.py (zero-I/O dataclass) can
import it without pulling in anything they don't already depend on.

Grown from six to eight codes (2026-08-14, GC discipline-split /
RTO-RE-QP handoff): QE and RTO added -- see each member's own comment
below for why. PI is untouched by this pass and stays exactly as it
was: no confirmed label, no default, no guess -- same fail-closed
treatment as ClaimPayload's jurisdiction_code (Airlock rejects a
missing/invalid jurisdiction_code outright rather than defaulting one
in; this module does the analogous thing for PI's label by simply
never supplying one).
"""
from enum import Enum


class AuthorityRoleType(str, Enum):
    """
    Licensed/registered regulatory authority roles recognized as
    verdict-changing authorities under the Supervisor Override
    Retirement principle. Full role definitions and PEB/MOM
    registration binding are explicitly future work -- not decided or
    encoded here (see CLAUDE.md's Open Items).

    PR (Permit Receiver) is deliberately NOT a member of this set: it
    names the executing worker/crew, not an approving or certifying
    role -- the same category as the Frontline Worker persona, not
    this authority set.
    """

    PE = "PE"
    QP = "QP"
    PI = "PI"
    PA = "PA"
    PM = "PM"
    SA = "SA"
    # QE (2026-08-14): named alongside QP as sharing the same
    # dormant-by-default / design-alteration-reactivation behavior (see
    # AuthorityBinding.activation in src/maestro/directory.py). Added as
    # its own code, not folded into QP, because it was named as a
    # distinct code in the handoff that requested it -- but what QE
    # expands to was not stated, so (same posture as PI/PA/PM/SA below)
    # no expansion is guessed here.
    QE = "QE"
    # RTO (2026-08-14): distinct from QP/PE per the same handoff --
    # functions as RE/QP's continuous on-site representative and is
    # externally verifiable via IES/ACES (as opposed to PE's PEB
    # verification). Both of those are the *reason* RTO gets its own
    # code rather than being merged into QP/PE, not a claim about what
    # the letters expand to -- the handoff gave the acronym and its
    # function, not a spelled-out name, so (same posture as PI/PA/PM/SA/
    # QE) role_type_label() below does not invent one. "RE" is
    # referenced here only as the role RTO stands in for; it is not
    # itself a member of this enum -- nothing in this pass asked for RE
    # to be modeled as its own authority role, and adding it
    # unprompted would be exactly the guessing this module avoids
    # elsewhere.
    RTO = "RTO"


# Human-readable label resolution (2026-08-06, Task A; extended
# 2026-08-14 by omission -- see below). Deliberately incomplete: only
# PE and QP are confidently known --
# - PE = "Professional Engineer": standard, unambiguous term, matches
#   the PEB (Professional Engineers Board) registration this repo's
#   handoffs have already named as PE's governing body.
# - QP = "Qualified Person": explicitly named as a defined Building
#   Control Act term in the handoff that introduced this authority
#   set -- not inferred, stated directly.
# PI, PA, PM, SA, QE, RTO are deliberately absent. "Safety Assessor"
# for SA was used as an illustrative example in the Task A instructions
# that requested this map -- an example is not a confirmed source, and
# nothing in this repo independently verifies it, so it is NOT entered
# here as fact. Same for any plausible-sounding expansion of PI/PA/PM
# ("Permit Issuer"/"Permit Approver"/"Project Manager" and similar), or
# of QE/RTO ("Qualified Engineer"/"Resident Technical Officer" and
# similar) -- plausible is not confirmed. role_type_label() falls back
# to the bare code for all six unconfirmed codes (the original four
# plus QE/RTO), which is what every screen already shows today for the
# original four -- this map does not regress anything, it only
# upgrades what's already confidently known.
ROLE_TYPE_LABELS: dict[AuthorityRoleType, str] = {
    AuthorityRoleType.PE: "Professional Engineer",
    AuthorityRoleType.QP: "Qualified Person",
}


def role_type_label(role_type: "AuthorityRoleType | None") -> "str | None":
    """
    Resolves role_type to its confirmed human-readable label, or the
    bare code if no confirmed label exists yet (see ROLE_TYPE_LABELS'
    own comment for exactly which four codes that applies to today).
    Returns None only if role_type itself is None -- callers with an
    untyped AuthorityBinding (role_type=None, e.g. the "General Duty
    Officer" catch-all) should use AuthorityBinding.role directly
    instead of calling this at all; that field is already a proper
    human-readable label with no role_type to resolve from.
    """
    if role_type is None:
        return None
    return ROLE_TYPE_LABELS.get(role_type, role_type.value)


class Discipline(str, Enum):
    """
    Discipline/vertical an AuthorityBinding covers (2026-08-14, GC
    discipline-split handoff): a GC's execution teams split by
    discipline -- one contact per discipline, not one person covering
    everything -- so a binding needs a discipline dimension distinct
    from its role_type. role_type answers "what kind of authority is
    this" (PE/QP/PI/PA/PM/SA/QE/RTO); discipline answers "which
    vertical of the work does this particular binding cover."

    CIVIL/STRUCTURAL/ELECTRICAL are the three disciplines named in the
    handoff that requested this -- named as representative examples
    ("civil, structural, electrical, etc."), not presented as a
    complete taxonomy of every discipline a GC might split by. Add more
    members only once a real, confirmed discipline is identified --
    same "don't invent what wasn't named" posture AuthorityRoleType
    above already takes with QE/RTO's unconfirmed label expansions.

    Lives in src/core/ alongside AuthorityRoleType, not
    src/maestro/directory.py, for the same reason AuthorityRoleType
    does: zero dependencies, importable by both Core and Maestro
    without either depending on the other.
    """

    CIVIL = "CIVIL"
    STRUCTURAL = "STRUCTURAL"
    ELECTRICAL = "ELECTRICAL"
