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
compatibility, no behavior change, same enum, same six members.

Zero dependencies (plain stdlib enum) so both src/core/models.py
(SQLAlchemy) and src/maestro/directory.py (zero-I/O dataclass) can
import it without pulling in anything they don't already depend on.
"""
from enum import Enum


class AuthorityRoleType(str, Enum):
    """
    Licensed/registered regulatory authority roles recognized as
    verdict-changing authorities under the Supervisor Override
    Retirement principle. Full role definitions and PEB/MOM
    registration binding are explicitly future work -- not decided or
    encoded here (see CLAUDE.md's Open Items) -- these are the six
    codes as specified, nothing more.

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


# Human-readable label resolution (2026-08-06, Task A). Deliberately
# incomplete: only PE and QP are confidently known --
# - PE = "Professional Engineer": standard, unambiguous term, matches
#   the PEB (Professional Engineers Board) registration this repo's
#   handoffs have already named as PE's governing body.
# - QP = "Qualified Person": explicitly named as a defined Building
#   Control Act term in the handoff that introduced this authority
#   set -- not inferred, stated directly.
# PI, PA, PM, SA are deliberately absent. "Safety Assessor" for SA was
# used as an illustrative example in the Task A instructions that
# requested this map -- an example is not a confirmed source, and
# nothing in this repo independently verifies it, so it is NOT entered
# here as fact. Same for any plausible-sounding expansion of PI/PA/PM
# ("Permit Issuer"/"Permit Approver"/"Project Manager" and similar) --
# plausible is not confirmed. role_type_label() falls back to the bare
# code for all four, which is what every screen already shows today --
# this map does not regress anything, it only upgrades what's already
# confidently known.
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
