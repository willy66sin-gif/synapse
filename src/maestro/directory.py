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
(PE/QP/PI/PA/PM/SA at the time -- see src/core/roles.py for the two
codes added since) that replaces override as the only way a verdict
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
enum, no behavior change) -- src/core/models.py's new IssuerRole
needed it too, and Core must not depend on Maestro to get it (see
src/core/roles.py's own doc comment for the full reasoning). Discipline
lives there too, for the identical reason, and is likewise re-exported
here.

Schema extension (2026-08-14, GC discipline-split / RTO-RE-QP
handoff) -- three additions, schema/structure only, same discipline as
the 2026-08-05 role_type restructuring above: no real PEB/IES/ACES
registration data is populated, DIRECTORY_MAP still ships with only
the untyped ("*", "*") catch-all plus the one confirmed R-ZONE-01
routing entry, and none of the three additions below are wired into
resolve_authority()'s lookup key or precedence logic -- that stays
(zone_id, reason_code) exactly as it was. Wiring any of them into live
lookup logic is a separate, not-yet-decided task, same posture
role_type itself took for a full cycle before check_authority() ever
read it.

1. discipline (Optional[Discipline], default None): a GC's execution
   teams split by discipline, not one person covering everything -- a
   binding now records which vertical (civil/structural/electrical/
   etc. -- see src/core/roles.py's Discipline) it covers, independent
   of its role_type. Not part of the DIRECTORY_MAP lookup key: adding a
   fourth lookup dimension is exactly the kind of live-logic redesign
   this pass is scoped to avoid, not a data-shape decision this pass
   should make unilaterally.

2. activation / activation_trigger (ActivationMode, default
   CONTINUOUS; Optional[str], default None): most bindings (the
   existing catch-all, R-ZONE-01's SA entry) represent continuous
   on-site presence -- unchanged, hence the CONTINUOUS default so
   existing bindings need no edit. QP and QE are different: dormant by
   default, reactivating only on a design-alteration trigger, not
   continuously present the way RTO (see below) or SA are.
   activation_trigger is a bare optional string, not a structured
   trigger-condition model -- no taxonomy of trigger types has been
   confirmed yet (same "bare field until a real shape is confirmed"
   posture as src/doctrine/models.py's citations column), so this
   holds a freeform description (e.g. "design_alteration") once a real
   dormant binding is added, not before.

3. RTO as a role_type (see src/core/roles.py's AuthorityRoleType.RTO):
   externally verifiable via IES/ACES rather than PEB, distinct from
   QP/PE, and -- per activation/activation_trigger above -- typically
   CONTINUOUS where QP/QE are typically TRIGGERED, since RTO functions
   as RE/QP's continuous on-site representative. That relationship
   ("RTO stands in for RE/QP on site") is documented here and on
   AuthorityRoleType.RTO's own comment, not encoded as a structural
   field: no confirmed shape for "represents another role" has been
   requested, and RE is not itself a modeled role (see
   AuthorityRoleType.RTO's comment for why).

PI is untouched by this pass. Same fail-closed treatment as
ClaimPayload's jurisdiction_code: required wherever a role_type is
PI, no default value, no guessed label -- see src/core/roles.py's own
comment for where that's enforced (by omission from ROLE_TYPE_LABELS).
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from src.core.roles import AuthorityRoleType, Discipline, role_type_label  # noqa: F401 - AuthorityRoleType/Discipline re-exported for backward compatibility


class ActivationMode(str, Enum):
    """
    Whether an AuthorityBinding represents continuous on-site presence
    or a dormant role that only activates on a specific trigger
    (2026-08-14, GC discipline-split / RTO-RE-QP handoff).

    CONTINUOUS is the default for every binding shipped today (the
    catch-all, R-ZONE-01's SA entry) -- unchanged behavior, no edit
    needed on existing entries. TRIGGERED describes QP/QE's dormant-
    by-default / design-alteration-reactivation behavior; see
    AuthorityBinding.activation_trigger for the (freeform, unpopulated)
    slot that would eventually describe what the trigger is.
    """

    CONTINUOUS = "CONTINUOUS"
    TRIGGERED = "TRIGGERED"


@dataclass(frozen=True)
class AuthorityBinding:
    binding_id: str
    role: str
    contact_id: Optional[str] = None
    role_type: Optional[AuthorityRoleType] = None
    discipline: Optional[Discipline] = None
    activation: ActivationMode = field(default=ActivationMode.CONTINUOUS)
    activation_trigger: Optional[str] = None


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
# licensed PE/QP/PI/PA/PM/SA/QE/RTO roles. Real (zone_id, reason_code)
# entries -- and real contact_id/discipline/activation values -- get
# added here as actual site authorities are identified and bound to
# real registrations.
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
