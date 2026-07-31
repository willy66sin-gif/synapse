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
from src.maestro.directory import AuthorityBinding, resolve_authority

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


def test_real_directory_map_resolves_via_catch_all_only():
    """Against the actual shipped DIRECTORY_MAP (not monkeypatched):
    since only the catch-all is seeded, every lookup -- regardless of
    zone_id/reason_code -- must resolve to it."""
    for zone_id, reason_code in [("ZONE-01", "R-PTW-01"), ("ZONE-99", "R-AUTH-01"), (None, None)]:
        result = resolve_authority(zone_id, reason_code)
        assert result.binding_id == "BIND-999"
        assert result.role == "General Duty Officer"
        assert result.contact_id is None


def test_resolve_authority_raises_if_catch_all_missing(monkeypatch):
    """Fail-closed: if DIRECTORY_MAP is ever misconfigured without even
    the catch-all, resolution must raise, not silently return an
    unresolved/incorrect binding."""
    monkeypatch.setattr(directory, "DIRECTORY_MAP", {})

    with pytest.raises(KeyError):
        resolve_authority("ZONE-01", "R-PTW-01")
