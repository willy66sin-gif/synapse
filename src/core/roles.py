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
