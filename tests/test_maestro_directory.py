"""
src/maestro/directory.py: resolve_authority() precedence tests.

resolve_authority() returns a list, not a single AuthorityBinding
(2026-08-18) -- the reason_code tier's single winner (most-specific
(zone_id, reason_code) match, then ("*", reason_code), then the
("*", "*") catch-all) comes first, followed by QP/QE's two bindings if
is_design_alteration=True. The two dimensions are orthogonal: both can
be true for the same claim at once.

To actually exercise the three-tier reason_code precedence order,
several tests monkeypatch DIRECTORY_MAP with a small multi-entry map
local to each test, rather than seeding the shipped module with
fabricated data.
"""
import pytest

from src.maestro import directory
from src.maestro.directory import (
    ActivationMode,
    AuthorityBinding,
    AuthorityRoleType,
    Discipline,
    resolve_authority,
    resolve_pa_authority,
)
from src.profiles.schemas import BaseProfileRef, CertifiedProfile, ProfileLineage

SPECIFIC = AuthorityBinding("BIND-001", "Zone A Safety Officer", "whatsapp:+6591234567")
REASON_DEFAULT = AuthorityBinding("BIND-101", "Duty WSO", "whatsapp:+6590000001")
CATCH_ALL = AuthorityBinding("BIND-999", "General Duty Officer", None)


@pytest.fixture
def multi_entry_directory(monkeypatch):
    monkeypatch.setattr(
        directory,
        "DIRECTORY_MAP",
        {
            ("ZONE_A", "R-PTW-01"): SPECIFIC,
            ("*", "R-PTW-01"): REASON_DEFAULT,
            ("*", "*"): CATCH_ALL,
        },
    )


def test_specific_zone_and_reason_match_wins_over_broader_entries(multi_entry_directory):
    result = resolve_authority("ZONE_A", "R-PTW-01")

    assert result == [SPECIFIC]


def test_global_reason_default_used_when_zone_has_no_specific_entry(multi_entry_directory):
    result = resolve_authority("ZONE_B", "R-PTW-01")

    assert result == [REASON_DEFAULT]


def test_catch_all_used_when_neither_zone_nor_reason_has_an_entry(multi_entry_directory):
    result = resolve_authority("ZONE_B", "R-AUTH-01")

    assert result == [CATCH_ALL]


def test_catch_all_used_when_reason_code_is_none(multi_entry_directory):
    """GO verdicts carry reason_code=None -- must still resolve, not raise."""
    result = resolve_authority("ZONE_A", None)

    assert result == [CATCH_ALL]


def test_precedence_order_prefers_more_specific_even_when_all_three_tiers_match(multi_entry_directory):
    """The real precedence-order regression: with all three tiers
    populated for the same (zone_id, reason_code) lookup, the most
    specific one must win, not the broadest or an arbitrary one."""
    result = resolve_authority("ZONE_A", "R-PTW-01")

    assert result == [SPECIFIC]
    assert result[0].binding_id == "BIND-001"
    assert SPECIFIC not in (REASON_DEFAULT, CATCH_ALL)


def test_real_directory_map_resolves_via_catch_all_for_a_genuinely_unrouted_reason_code():
    """Against the actual shipped DIRECTORY_MAP (not monkeypatched): no
    reason_code Core's current rule set can actually produce is left
    unrouted anymore as of 2026-08-18 -- R-ZONE-01 routes to SA,
    reason_code=None (GO) and R-PTW-01/R-AUTH-01/02/03 all route to RTO
    (direct confirmation -- see directory.py's own comment for the
    full history, including the earlier revert). The catch-all itself
    is untouched and still required to exist: this exercises it via a
    synthetic reason_code no real Verdict ever produces, confirming
    fallback behavior still works for whatever is NOT explicitly named
    in DIRECTORY_MAP. is_design_alteration defaults to False, so the
    result is still a single-element list."""
    result = resolve_authority("ZONE-01", "R-DOES-NOT-EXIST-99")

    assert len(result) == 1
    assert result[0].binding_id == "BIND-999"
    assert result[0].role == "General Duty Officer"
    assert result[0].contact_id is None
    assert result[0].role_type is None


def test_real_directory_map_routes_all_five_confirmed_reason_codes_to_rto():
    """2026-08-18, direct confirmation (explicit, not speculative):
    reason_code=None (GO) and R-PTW-01/R-AUTH-01/R-AUTH-02/R-AUTH-03 all
    resolve to RTO -- superseding the earlier "R-PTW-01 stays unrouted
    pending PA confirmation" and "no PE/QP/PA/PM/SA role honestly fits
    R-AUTH-01" reasoning (see directory.py's own comment for the full
    history). R-ZONE-01 is deliberately excluded here -- it keeps its
    own earlier, unrelated routing to SA."""
    for zone_id, reason_code in [
        ("ZONE-01", None),
        ("ZONE-99", None),
        (None, None),
        ("ZONE-01", "R-PTW-01"),
        ("ZONE-99", "R-AUTH-01"),
        ("ZONE-01", "R-AUTH-02"),
        ("ZONE-01", "R-AUTH-03"),
    ]:
        result = resolve_authority(zone_id, reason_code)
        assert len(result) == 1
        assert result[0].binding_id == "BIND-RTO-01"
        assert result[0].role == "RTO"
        assert result[0].role_type == AuthorityRoleType.RTO


def test_real_directory_map_routes_r_zone_01_to_sa():
    """The one confirmed reason_code routing entry (2026-08-06, Task 3
    of the Authority Role Model handoff): R-ZONE-01 resolves to SA via
    the ("*", "R-ZONE-01") global-reason-code-default tier, not the
    catch-all -- for any zone_id, since no more-specific (zone_id,
    reason_code) entry is seeded either."""
    for zone_id in ("ZONE-01", "ZONE-99", None):
        result = resolve_authority(zone_id, "R-ZONE-01")
        assert len(result) == 1
        assert result[0].binding_id == "BIND-SA-01"
        assert result[0].role == "SA"
        assert result[0].role_type == AuthorityRoleType.SA


def test_resolve_authority_raises_if_catch_all_missing(monkeypatch):
    """Fail-closed: if DIRECTORY_MAP is ever misconfigured without even
    the catch-all, resolution must raise, not silently return an
    unresolved/incorrect binding."""
    monkeypatch.setattr(directory, "DIRECTORY_MAP", {})

    with pytest.raises(KeyError):
        resolve_authority("ZONE-01", "R-PTW-01")


# --- Supervisor Override Retirement (2026-08-05): role_type schema restructuring ---


def test_real_catch_all_has_no_role_type():
    """The shipped catch-all is deliberately untyped: "General Duty
    Officer" is not one of the licensed PE/QP/PA/PM/SA/QE/RTO roles.
    Exercised via a synthetic reason_code no real Verdict produces --
    R-PTW-01 no longer reaches the catch-all as of 2026-08-18 (it now
    routes to RTO, see the routing-expansion tests above)."""
    result = resolve_authority("ZONE-01", "R-DOES-NOT-EXIST-99")

    assert result[0].role_type is None


def test_authority_binding_accepts_a_role_type():
    """Schema now supports a typed entry -- not populated in
    DIRECTORY_MAP yet, but the shape exists for when real PE/QP/PA/PM/
    SA entries are added (a separate, future task)."""
    binding = AuthorityBinding("BIND-PE-01", "Engineer of Record", "whatsapp:+6591112222", AuthorityRoleType.PE)

    assert binding.role_type == AuthorityRoleType.PE


def test_authority_role_type_has_exactly_the_seven_confirmed_codes():
    """PR (Permit Receiver) must never be a member: it names the
    executing worker/crew, the same category as the Frontline Worker
    persona, not an approving/certifying authority. Grown from six to
    eight (2026-08-14, GC discipline-split / RTO-RE-QP handoff), then
    back to seven the same day when PI was removed as a confirmed
    category error (it named an academic research role, not a
    construction authority) -- see tests/test_core_roles.py for the
    fuller version of this guard, including the label-fallback
    assertions for QE/RTO and the dedicated PI-non-reintroduction
    guard."""
    codes = {member.value for member in AuthorityRoleType}

    assert codes == {"PE", "QP", "PA", "PM", "SA", "QE", "RTO"}
    assert "PR" not in codes
    assert "PI" not in codes


# --- GC discipline-split / RTO-RE-QP handoff (2026-08-14): schema extension ---


def test_authority_binding_defaults_to_continuous_with_no_discipline():
    """Existing bindings (the real catch-all, R-ZONE-01's SA entry)
    must keep working unedited -- the new fields default to exactly
    what those bindings already implicitly were: no discipline split,
    continuously present, no reactivation trigger."""
    binding = AuthorityBinding("BIND-999", "General Duty Officer")

    assert binding.discipline is None
    assert binding.activation == ActivationMode.CONTINUOUS
    assert binding.activation_trigger is None


def test_authority_binding_accepts_a_discipline():
    """Schema now supports a discipline dimension -- not populated in
    DIRECTORY_MAP yet (no real GC team roster exists in this repo),
    but the shape exists for when real per-discipline contacts are
    added."""
    binding = AuthorityBinding(
        "BIND-STR-01",
        "Structural Discipline Lead",
        discipline=Discipline.STRUCTURAL,
    )

    assert binding.discipline == Discipline.STRUCTURAL


def test_authority_binding_accepts_a_triggered_activation_with_a_trigger_description():
    """Schema now supports dormant-by-default / reactivation-on-trigger
    roles -- this is exactly QP/QE's real, live shape as of 2026-08-18
    (see the routing-expansion tests below)."""
    binding = AuthorityBinding(
        "BIND-QP-01",
        "Qualified Person",
        role_type=AuthorityRoleType.QP,
        activation=ActivationMode.TRIGGERED,
        activation_trigger="design_alteration",
    )

    assert binding.activation == ActivationMode.TRIGGERED
    assert binding.activation_trigger == "design_alteration"


def test_real_directory_map_bindings_are_still_continuous_with_no_discipline():
    """Regression guard against this pass's own scope: the reason_code
    tier's real, shipped bindings must not have gained fabricated
    discipline data as a side effect of adding the field. Checks the
    true catch-all (via a synthetic, never-real reason_code -- R-PTW-01
    no longer reaches it as of 2026-08-18), R-ZONE-01's SA binding, and
    R-PTW-01's RTO binding -- all CONTINUOUS, unlike QP/QE (see below)."""
    catch_all = resolve_authority("ZONE-01", "R-DOES-NOT-EXIST-99")[0]
    zone_safety = resolve_authority("ZONE-01", "R-ZONE-01")[0]
    ptw_authority = resolve_authority("ZONE-01", "R-PTW-01")[0]

    for binding in (catch_all, zone_safety, ptw_authority):
        assert binding.discipline is None
        assert binding.activation == ActivationMode.CONTINUOUS
        assert binding.activation_trigger is None


# --- Routing expansion (2026-08-18): RTO live default, QP/QE live design-alteration routing, PM/PA unrouted ---


def test_qp_qe_do_not_appear_when_is_design_alteration_is_false():
    """No real Verdict.reason_code (R-PTW-01, R-AUTH-01/02/03, R-ZONE-01,
    or GO's None), combined with is_design_alteration left at its
    default (False), ever includes QP or QE in the result -- this is
    the "does NOT fire without the flag" half of the requirement."""
    for zone_id, reason_code in [
        ("ZONE-01", "R-PTW-01"),
        ("ZONE-99", "R-AUTH-01"),
        ("ZONE-01", "R-AUTH-02"),
        ("ZONE-01", "R-AUTH-03"),
        ("ZONE-01", "R-ZONE-01"),
        ("ZONE-01", None),
    ]:
        result = resolve_authority(zone_id, reason_code, is_design_alteration=False)
        role_types = {binding.role_type for binding in result}
        assert AuthorityRoleType.QP not in role_types
        assert AuthorityRoleType.QE not in role_types
        assert len(result) == 1


def test_qp_qe_both_appear_when_is_design_alteration_is_true():
    """The other half of the requirement: is_design_alteration=True
    always appends BOTH QP and QE (2026-08-18, explicit confirmation --
    nothing in this repo distinguishes a "QP-type" from a "QE-type"
    alteration, so both fire together), regardless of reason_code --
    orthogonal dimensions, confirmed explicitly to both be able to
    apply at once."""
    for zone_id, reason_code in [
        ("ZONE-01", "R-PTW-01"),
        ("ZONE-99", "R-AUTH-01"),
        ("ZONE-01", "R-ZONE-01"),
        ("ZONE-01", None),
    ]:
        result = resolve_authority(zone_id, reason_code, is_design_alteration=True)
        role_types = [binding.role_type for binding in result]
        assert AuthorityRoleType.QP in role_types
        assert AuthorityRoleType.QE in role_types
        assert len(result) == 3


def test_reason_code_and_design_alteration_bindings_coexist_in_order():
    """The reason_code-tier binding always comes first, QP then QE
    after -- e.g. a GO claim that's also a design alteration resolves
    to [RTO, QP-binding, QE-binding], not one or the other (explicit
    instruction: the two dimensions are orthogonal, both can be true at
    once)."""
    result = resolve_authority("ZONE-01", None, is_design_alteration=True)

    assert [binding.binding_id for binding in result] == ["BIND-RTO-01", "BIND-QP-DA-01", "BIND-QE-DA-01"]
    assert [binding.role_type for binding in result] == [
        AuthorityRoleType.RTO,
        AuthorityRoleType.QP,
        AuthorityRoleType.QE,
    ]


def test_qp_qe_bindings_are_triggered_not_continuous():
    """QP/QE's real, live shape: TRIGGERED activation with a
    "design_alteration" trigger description, distinct from every
    reason_code-tier binding (all CONTINUOUS, see the regression guard
    above)."""
    result = resolve_authority("ZONE-01", None, is_design_alteration=True)
    qp_binding = next(binding for binding in result if binding.role_type == AuthorityRoleType.QP)
    qe_binding = next(binding for binding in result if binding.role_type == AuthorityRoleType.QE)

    for binding in (qp_binding, qe_binding):
        assert binding.activation == ActivationMode.TRIGGERED
        assert binding.activation_trigger == "design_alteration"


def test_qp_qe_are_not_reachable_via_any_directory_map_key():
    """As of 2026-08-18, QP/QE are a separate lookup dimension
    (is_design_alteration), not a (zone_id, reason_code) key -- the old
    placeholder keys ("DESIGN_ALTERATION_QP"/"_QE") are gone from
    DIRECTORY_MAP entirely, and no role_type in the map itself is ever
    QP or QE."""
    role_types_in_map = {binding.role_type for binding in directory.DIRECTORY_MAP.values()}

    assert AuthorityRoleType.QP not in role_types_in_map
    assert AuthorityRoleType.QE not in role_types_in_map


def test_pm_and_pa_have_no_directory_entries():
    """PM and PA both have no DIRECTORY_MAP entry, for two different
    reasons. PM: in-situ operational decisions pass through RTO's gate
    rather than routing independently -- genuinely unrouted. PA: as of
    2026-09-03, PA resolves dynamically per project via
    resolve_pa_authority() (see directory.py), keyed on the claim's
    Certified Profile rather than (zone_id, reason_code) -- so it has
    no DIRECTORY_MAP entry to add, not because it's still unconfirmed.
    Neither role_type appears on any binding anywhere in the real,
    shipped DIRECTORY_MAP (which no longer contains QP/QE either, per
    the test above -- this checks PM/PA specifically, not "anything
    beyond the reason_code tier")."""
    role_types_in_use = {binding.role_type for binding in directory.DIRECTORY_MAP.values()}

    assert AuthorityRoleType.PM not in role_types_in_use
    assert AuthorityRoleType.PA not in role_types_in_use


# --- PA authority resolution (2026-09-03): per-project liability assignment, resolved ---


def test_resolve_pa_authority_reads_the_certified_profiles_accountable_architect():
    """PA's real, live shape: dynamically built from the Certified
    Profile submitted for the project, not looked up in DIRECTORY_MAP
    -- the accountable_architect declared on that profile becomes the
    binding's contact_id."""
    profile = CertifiedProfile(
        profile_id="SG-BC-2024",
        jurisdiction_code="SG",
        version="2024",
        lineage=ProfileLineage.STANDALONE,
        parameters={},
        accountable_architect="Jane Tan, ARB-1234",
    )

    binding = resolve_pa_authority(profile)

    assert binding.contact_id == "Jane Tan, ARB-1234"
    assert binding.role_type == AuthorityRoleType.PA
    assert binding.binding_id == "BIND-PA-SG-BC-2024"


def test_resolve_pa_authority_varies_by_project_not_a_fixed_catalog_entry():
    """Unlike SA/RTO (one fixed AuthorityBinding regardless of which
    project's claim triggers it), two different projects' Certified
    Profiles resolve to two different PA bindings -- liability
    assignment is per-project, not a shared static role."""
    profile_a = CertifiedProfile(
        profile_id="SG-BC-2024",
        jurisdiction_code="SG",
        version="2024",
        lineage=ProfileLineage.STANDALONE,
        parameters={},
        accountable_architect="Jane Tan, ARB-1234",
    )
    profile_b = CertifiedProfile(
        profile_id="DE-EC2-ANNEX",
        jurisdiction_code="DE",
        version="2024",
        lineage=ProfileLineage.BASE_ANNEX,
        base_ref=BaseProfileRef(base_profile_id="EUROCODE-EC2-1-1", base_profile_version="2004+A1:2014"),
        parameters={},
        accountable_architect="Markus Weber, AKNW-5678",
    )

    binding_a = resolve_pa_authority(profile_a)
    binding_b = resolve_pa_authority(profile_b)

    assert binding_a.contact_id != binding_b.contact_id
    assert binding_a.binding_id != binding_b.binding_id


def test_pa_authority_not_reachable_via_resolve_authority():
    """PA-gating is a third, orthogonal dimension keyed on the claim's
    Certified Profile -- not folded into resolve_authority()'s
    (zone_id, reason_code) lookup or its is_design_alteration flag, the
    same way QP/QE are folded in. No combination of resolve_authority()
    arguments can produce a PA binding; callers needing one call
    resolve_pa_authority() directly."""
    for zone_id, reason_code, is_design_alteration in [
        ("ZONE-01", "R-PTW-01", False),
        ("ZONE-01", "R-PTW-01", True),
        ("ZONE-01", "R-ZONE-01", False),
        ("ZONE-01", None, True),
    ]:
        result = resolve_authority(zone_id, reason_code, is_design_alteration=is_design_alteration)
        role_types = {binding.role_type for binding in result}
        assert AuthorityRoleType.PA not in role_types
