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

PI REMOVED (2026-08-14, same day as the above, confirmed category
error): "PI" here referred to Principal Investigator, an academic
research role, not a construction-authority gate -- see
src/core/roles.py's own doc comment for the full reasoning. Every
reference to PI below this point in the module's history (the
R-PTW-01 routing comment) is updated to reflect that it's gone, not
merely unconfirmed.
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
# R-ZONE-01 -> SA was the first mapping confirmed unambiguous
# ("R-ZONE-01 -> SA are clean", per the handoff that introduced the
# five reason codes this routes). R-PTW-01 and R-AUTH-01/02/03 -> RTO
# were confirmed later (2026-08-18, direct confirmation -- see the
# routing-expansion comment block below for the full history,
# including the superseded R-PTW-01/PA reasoning this replaces). No
# reason code below this point is left deliberately unrouted anymore --
# see the routing-expansion comment block for exactly what changed and
# why.
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

# Routing expansion (2026-08-18, GC discipline-split follow-up --
# confirmed by both Ganesh and Ben Chin, asserted grounding per this
# file's own Execution Evidence Authority discipline, not repo-verified
# fact). Three decisions, two now live, one still not:
#
# 1. RTO as the live routing target for reason_code=None (GO) and for
#    R-PTW-01/R-AUTH-01/R-AUTH-02/R-AUTH-03 (2026-08-18, direct
#    confirmation -- explicit, not speculative). History: the
#    reason_code=None wiring was first added, then REVERTED the same
#    day -- it had been implemented unilaterally after being correctly
#    flagged as a real architecture question (via AskUserQuestion) but
#    without waiting for actual sign-off on the answer. RTO was made
#    structural-only for a pass (an inert placeholder key, matching
#    QP/QE's status) pending that sign-off. It has since been given,
#    explicitly and for all five cases together, so all five are now
#    live: ("*", None), ("*", "R-PTW-01"), ("*", "R-AUTH-01"),
#    ("*", "R-AUTH-02"), ("*", "R-AUTH-03") all resolve to
#    _CONTINUOUS_COMPLIANCE_AUTHORITY (BIND-RTO-01). This supersedes
#    the earlier R-PTW-01/PA reasoning above (R-PTW-01 was left
#    unrouted pending independent PA confirmation -- that question is
#    now moot for routing purposes, since R-PTW-01 routes to RTO
#    instead, not because PA was ever confirmed) and the earlier
#    R-AUTH-01/02/03 reasoning (no PE/QP/PA/PM/SA role was an honest
#    fit for "we don't know who submitted this" -- RTO, confirmed
#    separately as the continuous on-site compliance gate, is the
#    answer that was actually given, not a domain-role guess). R-ZONE-01
#    is deliberately NOT included -- it already has its own confirmed,
#    unrelated routing to SA (above), untouched by this change. The
#    ("*", "*") catch-all itself is untouched and still required to
#    exist -- it remains the fallback for any reason_code not
#    explicitly named here (there is none among Core's current rule set
#    left unrouted today, but the catch-all still guards against a
#    future, not-yet-named one).
#
# 2. QP/QE stay TRIGGERED, not CONTINUOUS -- structural entries only,
#    same "ship the shape, not fabricated behavior" discipline as
#    discipline/activation themselves (2026-08-14, still unwired into
#    resolve_authority()'s lookup key or precedence logic). There is no
#    ClaimPayload field carrying a design-alteration signal today and
#    no resolve_authority() parameter to read one -- adding either is a
#    real schema/signature change, explicitly out of scope this pass.
#    _DESIGN_ALTERATION_QP_AUTHORITY / _DESIGN_ALTERATION_QE_AUTHORITY
#    below are inserted under placeholder keys that no real
#    Verdict.reason_code value will ever produce (Core's rule set never
#    emits "DESIGN_ALTERATION_QP"/"DESIGN_ALTERATION_QE" as a
#    reason_code) -- present for future wiring, inert against all live
#    traffic today. Do not wire a live design-alteration check by
#    reusing these keys as a shortcut; that's the separate,
#    not-yet-decided task this comment (and the 2026-08-14 one above
#    it) already flags.
#
# 3. PM and PA remain unrouted -- deliberately no AuthorityBinding
#    entry for either, for two different reasons, neither a placeholder
#    omission:
#      - PM: in-situ operational decisions pass through RTO's gate
#        rather than routing independently -- PM is not a separate
#        escalation target, its ground is already covered by (1) above.
#      - PA: per-project liability assignment is still unconfirmed (see
#        CLAUDE.md's Open Items -- the original R-PTW-01 handoff paired
#        "PI/PA" together, not a single role, and PI has since been
#        removed as a category error, which eliminates one option
#        without confirming the other). Do not add a PA binding until
#        that liability question actually resolves.
_CONTINUOUS_COMPLIANCE_AUTHORITY = AuthorityBinding(
    "BIND-RTO-01",
    # Bare code "RTO" -- unconfirmed label, same posture as SA above
    # (ROLE_TYPE_LABELS only has PE/QP confirmed; see src/core/roles.py).
    role_type_label(AuthorityRoleType.RTO),
    None,
    AuthorityRoleType.RTO,
    None,
    # CONTINUOUS reflects what RTO actually is (RE/QP's continuous
    # on-site representative, per point 1 above) -- true regardless of
    # which reason_code keys route to it below.
    ActivationMode.CONTINUOUS,
)

_DESIGN_ALTERATION_QP_AUTHORITY = AuthorityBinding(
    "BIND-QP-DA-01",
    role_type_label(AuthorityRoleType.QP),
    None,
    AuthorityRoleType.QP,
    None,
    ActivationMode.TRIGGERED,
    "design_alteration",
)

_DESIGN_ALTERATION_QE_AUTHORITY = AuthorityBinding(
    "BIND-QE-DA-01",
    # Bare code "QE" -- unconfirmed label, same posture as SA/RTO above.
    role_type_label(AuthorityRoleType.QE),
    None,
    AuthorityRoleType.QE,
    None,
    ActivationMode.TRIGGERED,
    "design_alteration",
)

# role_type intentionally left None on the catch-all -- "General Duty
# Officer" is not one of the licensed PE/QP/PA/PM/SA/QE/RTO roles. It
# remains the required fallback (resolve_authority() fails closed if
# it's ever missing) for any reason_code not explicitly routed below --
# not removed, just no longer hit by any of Core's current rule set.
#
# Expanded 2026-08-18 (see the routing-expansion comment block above):
# RTO is now the live target for five entries -- reason_code=None (GO)
# and R-PTW-01/R-AUTH-01/R-AUTH-02/R-AUTH-03 -- confirmed directly, not
# speculative. R-ZONE-01 keeps its own separate, earlier-confirmed
# routing to SA, untouched. QP/QE's two placeholder-keyed,
# structurally-TRIGGERED entries (point 2 above) are unaffected by this
# expansion. PM and PA remain absent entirely, per point 3 above.
DIRECTORY_MAP: dict[tuple[Optional[str], Optional[str]], AuthorityBinding] = {
    ("*", "*"): AuthorityBinding("BIND-999", "General Duty Officer", None, None),
    ("*", "R-ZONE-01"): _ZONE_SAFETY_AUTHORITY,
    ("*", None): _CONTINUOUS_COMPLIANCE_AUTHORITY,
    ("*", "R-PTW-01"): _CONTINUOUS_COMPLIANCE_AUTHORITY,
    ("*", "R-AUTH-01"): _CONTINUOUS_COMPLIANCE_AUTHORITY,
    ("*", "R-AUTH-02"): _CONTINUOUS_COMPLIANCE_AUTHORITY,
    ("*", "R-AUTH-03"): _CONTINUOUS_COMPLIANCE_AUTHORITY,
    ("*", "DESIGN_ALTERATION_QP"): _DESIGN_ALTERATION_QP_AUTHORITY,
    ("*", "DESIGN_ALTERATION_QE"): _DESIGN_ALTERATION_QE_AUTHORITY,
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
