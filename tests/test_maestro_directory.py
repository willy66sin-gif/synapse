"""
src/maestro/directory.py: resolve_authority() precedence tests.

The real DIRECTORY_MAP ships with exactly one entry (the ("*", "*")
catch-all) -- see directory.py's own comment on why. To actually
exercise the three-tier precedence order (specific zone+reason beats
global reason-code default beats catch-all), these tests monkeypatch
DIRECTORY_MAP with a small multi-entry map local to each test, rather
than seeding the shipped module with fabricated data.
"""
import pytest

from src.maestro import directory
from src.maestro.directory import ActivationMode, AuthorityBinding, AuthorityRoleType, Discipline, resolve_authority

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

    assert result == SPECIFIC


def test_global_reason_default_used_when_zone_has_no_specific_entry(multi_entry_directory):
    result = resolve_authority("ZONE_B", "R-PTW-01")

    assert result == REASON_DEFAULT


def test_catch_all_used_when_neither_zone_nor_reason_has_an_entry(multi_entry_directory):
    result = resolve_authority("ZONE_B", "R-AUTH-01")

    assert result == CATCH_ALL


def test_catch_all_used_when_reason_code_is_none(multi_entry_directory):
    """GO verdicts carry reason_code=None -- must still resolve, not raise."""
    result = resolve_authority("ZONE_A", None)

    assert result == CATCH_ALL


def test_precedence_order_prefers_more_specific_even_when_all_three_tiers_match(multi_entry_directory):
    """The real precedence-order regression: with all three tiers
    populated for the same (zone_id, reason_code) lookup, the most
    specific one must win, not the broadest or an arbitrary one."""
    result = resolve_authority("ZONE_A", "R-PTW-01")

    assert result.binding_id == "BIND-001"
    assert result != REASON_DEFAULT
    assert result != CATCH_ALL


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
    in DIRECTORY_MAP."""
    result = resolve_authority("ZONE-01", "R-DOES-NOT-EXIST-99")

    assert result.binding_id == "BIND-999"
    assert result.role == "General Duty Officer"
    assert result.contact_id is None
    assert result.role_type is None


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
        assert result.binding_id == "BIND-RTO-01"
        assert result.role == "RTO"
        assert result.role_type == AuthorityRoleType.RTO


def test_real_directory_map_routes_r_zone_01_to_sa():
    """The one confirmed reason_code routing entry (2026-08-06, Task 3
    of the Authority Role Model handoff): R-ZONE-01 resolves to SA via
    the ("*", "R-ZONE-01") global-reason-code-default tier, not the
    catch-all -- for any zone_id, since no more-specific (zone_id,
    reason_code) entry is seeded either."""
    for zone_id in ("ZONE-01", "ZONE-99", None):
        result = resolve_authority(zone_id, "R-ZONE-01")
        assert result.binding_id == "BIND-SA-01"
        assert result.role == "SA"
        assert result.role_type == AuthorityRoleType.SA


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

    assert result.role_type is None


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
    roles (QP/QE's real behavior) -- not populated in DIRECTORY_MAP
    yet, but the shape exists."""
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
    """Regression guard against this pass's own scope: the real,
    shipped DIRECTORY_MAP must not have gained fabricated discipline/
    activation data as a side effect of adding the fields. Checks the
    true catch-all (via a synthetic, never-real reason_code -- R-PTW-01
    no longer reaches it as of 2026-08-18, see the routing-expansion
    tests above), R-ZONE-01's SA binding, and R-PTW-01's RTO binding."""
    catch_all = resolve_authority("ZONE-01", "R-DOES-NOT-EXIST-99")
    zone_safety = resolve_authority("ZONE-01", "R-ZONE-01")
    ptw_authority = resolve_authority("ZONE-01", "R-PTW-01")

    for binding in (catch_all, zone_safety, ptw_authority):
        assert binding.discipline is None
        assert binding.activation == ActivationMode.CONTINUOUS
        assert binding.activation_trigger is None


# --- Routing expansion (2026-08-18): RTO default, QP/QE structural-only, PM/PA unrouted ---




def test_qp_qe_routing_does_not_fire_for_any_real_reason_code():
    """No real Verdict.reason_code (R-PTW-01, R-AUTH-01/02/03, R-ZONE-01,
    or GO's None) ever resolves to QP or QE -- there is no live
    design-alteration signal anywhere in this codebase yet (no
    ClaimPayload field, no resolve_authority() parameter), so QP/QE must
    never fire for ordinary traffic. This is the "does NOT fire without
    a design-alteration flag" half of the requirement."""
    for zone_id, reason_code in [
        ("ZONE-01", "R-PTW-01"),
        ("ZONE-99", "R-AUTH-01"),
        ("ZONE-01", "R-AUTH-02"),
        ("ZONE-01", "R-AUTH-03"),
        ("ZONE-01", "R-ZONE-01"),
        ("ZONE-01", None),
    ]:
        result = resolve_authority(zone_id, reason_code)
        assert result.role_type not in (AuthorityRoleType.QP, AuthorityRoleType.QE)


def test_qp_qe_routing_fires_only_via_its_own_placeholder_trigger_key():
    """The other half of the requirement: QP/QE routing DOES fire, but
    only for the dedicated placeholder key that stands in for a real
    design-alteration flag -- no live ClaimPayload field or
    resolve_authority() parameter carries that signal yet (see
    directory.py's 2026-08-18 comment), so this exercises the entries'
    reachability through the existing (zone_id, reason_code) mechanism
    directly, not a live conditional dispatch from real claim data."""
    qp_result = resolve_authority("*", "DESIGN_ALTERATION_QP")
    assert qp_result.binding_id == "BIND-QP-DA-01"
    assert qp_result.role_type == AuthorityRoleType.QP
    assert qp_result.activation == ActivationMode.TRIGGERED
    assert qp_result.activation_trigger == "design_alteration"

    qe_result = resolve_authority("*", "DESIGN_ALTERATION_QE")
    assert qe_result.binding_id == "BIND-QE-DA-01"
    assert qe_result.role_type == AuthorityRoleType.QE
    assert qe_result.activation == ActivationMode.TRIGGERED
    assert qe_result.activation_trigger == "design_alteration"


def test_pm_and_pa_have_no_directory_entries():
    """PM and PA are deliberately unrouted -- not skipped, asserted
    absent. PM: in-situ operational decisions pass through RTO's gate
    rather than routing independently. PA: per-project liability
    assignment is still unconfirmed (see CLAUDE.md's Open Items).
    Neither role_type appears on any binding anywhere in the real,
    shipped DIRECTORY_MAP."""
    role_types_in_use = {binding.role_type for binding in directory.DIRECTORY_MAP.values()}

    assert AuthorityRoleType.PM not in role_types_in_use
    assert AuthorityRoleType.PA not in role_types_in_use
