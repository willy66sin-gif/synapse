"""
Deterministic authority-escalation directory.

Per the Escalation Ownership Principle (CLAUDE.md): who owns
escalating a rejected claim is determined by the adjudicated failure
reason (reason_code), not by the work activity that was attempted
(claim_type/work_type) -- a PTW failure in a zone and an authority
failure in that same zone are two different people's problem, even
though they happened in the same place.

Zero I/O, zero external calls -- a pure in-memory lookup, same
discipline as src/core/rules.py and src/supervisor/logic.py.

Schema restructuring (2026-08-05, Supervisor Override Retirement --
see CLAUDE.md): AuthorityBinding gained role_type, an optional
AuthorityRoleType drawn from the licensed/registered authority set
(PE/QP/PI/PA/PM/SA) that replaces override as the only way a verdict
can change -- a fresh, re-adjudicated claim from the specific role
that owns the gate, not a generic label. This is schema/structure
only, per this pass's explicit scope: no real PEB/MOM registration
data is populated here, and DIRECTORY_MAP still ships with only the
untyped ("*", "*") catch-all (role_type=None) -- removing it before
real typed entries exist would fail every Maestro alert and the
Frontline Worker screen closed (they all call resolve_authority()),
which is a different, larger decision than this pass's scope. See
CLAUDE.md's Open Items for both the role-to-registration binding and
the reason_code -> role_type routing table, neither decided here.

Relocated (2026-08-06): AuthorityRoleType itself now lives in
src/core/roles.py, re-exported here for backward compatibility (same
enum, same six members, no behavior change) -- src/core/models.py's
new IssuerRole needed it too, and Core must not depend on Maestro to
get it (see src/core/roles.py's own doc comment for the full reasoning).
"""
from dataclasses import dataclass
from typing import Optional

from src.core.roles import AuthorityRoleType  # noqa: F401 - re-exported for backward compatibility


@dataclass(frozen=True)
class AuthorityBinding:
    binding_id: str
    role: str
    contact_id: Optional[str] = None
    role_type: Optional[AuthorityRoleType] = None


# Deprecated (2026-08-05, Supervisor Override Retirement): no longer
# referenced by src/maestro/schemas.py's escalation_contact -- pointing
# an escalation at a retired POST /supervisor/override would be
# actively misleading. Left defined, not deleted, only because the
# route itself still exists at this path pending formal removal (see
# CLAUDE.md's Supervisor Override Retirement principle).
SUPERVISOR_OVERRIDE_URL = "https://synapse.local/supervisor/override"


# Starts with exactly one entry: the untyped catch-all default.
# role_type intentionally left None here -- "General Duty Officer" is
# not one of the licensed PE/QP/PI/PA/PM/SA roles, and no real typed
# entries are populated in this pass (schema/structure only). Real
# (zone_id, reason_code) and ("*", reason_code) entries -- with real
# role_type values -- get added here as actual site authorities are
# identified and bound to real registrations; contact_id stays None
# until a real contact channel exists.
DIRECTORY_MAP: dict[tuple[Optional[str], Optional[str]], AuthorityBinding] = {
    ("*", "*"): AuthorityBinding("BIND-999", "General Duty Officer", None, None),
}


def resolve_authority(zone_id: Optional[str], reason_code: Optional[str]) -> AuthorityBinding:
    """
    Precedence, most to least specific:
      1. (zone_id, reason_code)  -- specific match
      2. ("*", reason_code)      -- global reason-code default
      3. ("*", "*")               -- catch-all system default

    The catch-all is required to exist in DIRECTORY_MAP; this function
    fails closed (raises) rather than returning an unresolved binding
    if it's ever missing, instead of silently falling through to None.
    """
    for key in ((zone_id, reason_code), ("*", reason_code), ("*", "*")):
        binding = DIRECTORY_MAP.get(key)
        if binding is not None:
            return binding

    raise KeyError(
        "No AuthorityBinding matched, not even the ('*', '*') catch-all -- DIRECTORY_MAP is misconfigured."
    )
