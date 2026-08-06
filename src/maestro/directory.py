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
CLAUDE.md's Open Items for the role-to-registration binding, still not
decided. The reason_code -> role_type routing table is now partially
decided (2026-08-06) -- see the DIRECTORY_MAP entries below for
exactly what is and isn't routed, and why.

Relocated (2026-08-06): AuthorityRoleType itself now lives in
src/core/roles.py, re-exported here for backward compatibility (same
enum, same six members, no behavior change) -- src/core/models.py's
new IssuerRole needed it too, and Core must not depend on Maestro to
get it (see src/core/roles.py's own doc comment for the full reasoning).
"""
from dataclasses import dataclass
from typing import Optional

from src.core.roles import AuthorityRoleType, role_type_label  # noqa: F401 - AuthorityRoleType re-exported for backward compatibility


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


# reason_code -> AuthorityRoleType routing (2026-08-06, Task 3 of the
# "Authority Role Model" handoff). Built on top of the precedence
# mechanism this file already had (the ("*", reason_code) tier exists
# specifically for this) -- not a separate table, since this IS a
# (zone_id, reason_code) lookup, just keyed generically on reason_code
# rather than a specific zone.
#
# Only R-ZONE-01 -> SA is added: it's the one mapping actually
# confirmed as unambiguous ("R-ZONE-01 -> SA are clean", per the
# handoff that introduced the five reason codes this routes). The
# other four reason codes are deliberately left unrouted (fall through
# to the ("*", "*") catch-all below), not guessed at:
#   - R-PTW-01: the same handoff paired it with "PI/PA" together, not
#     a single role -- AuthorityBinding.role_type is one value per
#     binding, and nothing confirms whether PI or PA (or either) is
#     the actual single answer. Routing it would mean picking one
#     without grounds to.
#   - R-AUTH-01/02/03: R-AUTH-01 is an unauthenticated-issuer failure,
#     not a domain/technical one -- no PE/QP/PI/PA/PM/SA role is an
#     honest fit for "we don't know who submitted this." R-AUTH-02/03
#     are new (2026-08-06, R-AUTH-01 disambiguation) and postdate the
#     "clean" assessment above entirely -- nothing has confirmed a
#     role for them yet, so nothing is asserted here.
# Each of these four can gain its own ("*", reason_code) entry later,
# the same way R-ZONE-01 just did, once/if a real answer exists.
_ZONE_SAFETY_AUTHORITY = AuthorityBinding(
    "BIND-SA-01",
    # Computed via role_type_label(), not a hardcoded literal -- today
    # this still renders as the bare code "SA", since SA has no
    # confirmed label in src/core/roles.py's ROLE_TYPE_LABELS (see that
    # module's own comment for why: no confirmed definition of what
    # "SA" stands for has been supplied anywhere in this repo or its
    # handoffs; inventing one would be asserting a translation with no
    # basis). Computing it this way means this binding's display label
    # updates automatically, with no edit needed here, the moment a
    # real SA label is confirmed and added to ROLE_TYPE_LABELS.
    role_type_label(AuthorityRoleType.SA),
    None,
    AuthorityRoleType.SA,
)

# Starts with two entries: the untyped catch-all default, and the one
# confirmed reason_code routing above. role_type intentionally left
# None on the catch-all -- "General Duty Officer" is not one of the
# licensed PE/QP/PI/PA/PM/SA roles. Real (zone_id, reason_code) entries
# -- and real contact_id values -- get added here as actual site
# authorities are identified and bound to real registrations.
DIRECTORY_MAP: dict[tuple[Optional[str], Optional[str]], AuthorityBinding] = {
    ("*", "*"): AuthorityBinding("BIND-999", "General Duty Officer", None, None),
    ("*", "R-ZONE-01"): _ZONE_SAFETY_AUTHORITY,
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
