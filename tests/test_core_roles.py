"""
src/core/roles.py: role_type_label() tests (2026-08-06, Task A).

ROLE_TYPE_LABELS is deliberately incomplete -- only PE and QP are
confidently known human-readable labels; PI/PA/PM/SA fall back to
their bare code. These tests assert that split explicitly, so a
future edit that guesses at one of the unconfirmed four gets caught
here rather than silently shipped.
"""
from src.core.roles import AuthorityRoleType, role_type_label


def test_confirmed_labels_resolve_to_human_readable_text():
    assert role_type_label(AuthorityRoleType.PE) == "Professional Engineer"
    assert role_type_label(AuthorityRoleType.QP) == "Qualified Person"


def test_unconfirmed_labels_fall_back_to_the_bare_code():
    """PI/PA/PM/SA have no confirmed expansion -- falling back to the
    code itself is the honest behavior, not a guess dressed up as a
    label."""
    assert role_type_label(AuthorityRoleType.PI) == "PI"
    assert role_type_label(AuthorityRoleType.PA) == "PA"
    assert role_type_label(AuthorityRoleType.PM) == "PM"
    assert role_type_label(AuthorityRoleType.SA) == "SA"


def test_none_role_type_resolves_to_none():
    """Distinct from an unconfirmed code: role_type=None means there's
    no role_type to resolve at all (e.g. the untyped "General Duty
    Officer" catch-all) -- callers should use AuthorityBinding.role
    directly in that case, not treat this as another fallback case."""
    assert role_type_label(None) is None
