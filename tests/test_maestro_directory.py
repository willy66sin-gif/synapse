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


def test_real_directory_map_resolves_via_catch_all_for_unrouted_reason_codes():
    """Against the actual shipped DIRECTORY_MAP (not monkeypatched):
    R-ZONE-01 has its own routing entry as of 2026-08-06 (see the
    dedicated test below) -- every OTHER reason_code, deliberately left
    unrouted (see DIRECTORY_MAP's own comment for exactly why each
    one), still falls through to the catch-all."""
    for zone_id, reason_code in [
        ("ZONE-01", "R-PTW-01"),
        ("ZONE-99", "R-AUTH-01"),
        ("ZONE-01", "R-AUTH-02"),
        ("ZONE-01", "R-AUTH-03"),
        (None, None),
    ]:
        result = resolve_authority(zone_id, reason_code)
        assert result.binding_id == "BIND-999"
        assert result.role == "General Duty Officer"
        assert result.contact_id is None
        assert result.role_type is None


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
    Officer" is not one of the licensed PE/QP/PI/PA/PM/SA roles, and no
    real typed entries are populated in this pass (schema/structure
    only -- see directory.py's own comment)."""
    result = resolve_authority("ZONE-01", "R-PTW-01")

    assert result.role_type is None


def test_authority_binding_accepts_a_role_type():
    """Schema now supports a typed entry -- not populated in
    DIRECTORY_MAP yet, but the shape exists for when real PE/QP/PI/PA/
    PM/SA entries are added (a separate, future task)."""
    binding = AuthorityBinding("BIND-PE-01", "Engineer of Record", "whatsapp:+6591112222", AuthorityRoleType.PE)

    assert binding.role_type == AuthorityRoleType.PE


def test_authority_role_type_has_exactly_the_eight_confirmed_codes():
    """PR (Permit Receiver) must never be a member: it names the
    executing worker/crew, the same category as the Frontline Worker
    persona, not an approving/certifying authority. Grown from six to
    eight (2026-08-14, GC discipline-split / RTO-RE-QP handoff) -- see
    tests/test_core_roles.py for the fuller version of this guard,
    including the label-fallback assertions for QE/RTO."""
    codes = {member.value for member in AuthorityRoleType}

    assert codes == {"PE", "QP", "PI", "PA", "PM", "SA", "QE", "RTO"}
    assert "PR" not in codes


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
    activation data as a side effect of adding the fields."""
    catch_all = resolve_authority("ZONE-01", "R-PTW-01")
    zone_safety = resolve_authority("ZONE-01", "R-ZONE-01")

    for binding in (catch_all, zone_safety):
        assert binding.discipline is None
        assert binding.activation == ActivationMode.CONTINUOUS
        assert binding.activation_trigger is None
