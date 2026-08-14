"""
src/core/roles.py: role_type_label() tests (2026-08-06, Task A;
extended 2026-08-14 for QE/RTO; PI removed 2026-08-14 as a confirmed
category error -- see AuthorityRoleType's own doc comment).

ROLE_TYPE_LABELS is deliberately incomplete -- only PE and QP are
confidently known human-readable labels; PA/PM/SA/QE/RTO fall back to
their bare code. These tests assert that split explicitly, so a
future edit that guesses at one of the unconfirmed five gets caught
here rather than silently shipped.
"""
from src.core.roles import AuthorityRoleType, Discipline, role_type_label


def test_confirmed_labels_resolve_to_human_readable_text():
    assert role_type_label(AuthorityRoleType.PE) == "Professional Engineer"
    assert role_type_label(AuthorityRoleType.QP) == "Qualified Person"


def test_unconfirmed_labels_fall_back_to_the_bare_code():
    """PA/PM/SA/QE/RTO have no confirmed expansion -- falling back to
    the code itself is the honest behavior, not a guess dressed up as
    a label. (PI is not included here -- it was never in this
    "unconfirmed but plausible" category; see
    test_pi_is_not_reintroduced_without_grounded_construction_authority_reason
    below.)"""
    assert role_type_label(AuthorityRoleType.PA) == "PA"
    assert role_type_label(AuthorityRoleType.PM) == "PM"
    assert role_type_label(AuthorityRoleType.SA) == "SA"
    assert role_type_label(AuthorityRoleType.QE) == "QE"
    assert role_type_label(AuthorityRoleType.RTO) == "RTO"


def test_none_role_type_resolves_to_none():
    """Distinct from an unconfirmed code: role_type=None means there's
    no role_type to resolve at all (e.g. the untyped "General Duty
    Officer" catch-all) -- callers should use AuthorityBinding.role
    directly in that case, not treat this as another fallback case."""
    assert role_type_label(None) is None


def test_authority_role_type_has_exactly_the_seven_confirmed_codes():
    """Grown from six to eight (2026-08-14, GC discipline-split /
    RTO-RE-QP handoff: QE and RTO added, both with no confirmed label
    -- see test_unconfirmed_labels_fall_back_to_the_bare_code), then
    back down to seven the same day when PI was removed as a confirmed
    category error. PR (Permit Receiver) must never be a member -- see
    AuthorityRoleType's own docstring -- and neither must RE, which
    this pass deliberately did not add (RTO represents RE/QP
    functionally but RE was never asked to be modeled as its own
    role)."""
    codes = {member.value for member in AuthorityRoleType}

    assert codes == {"PE", "QP", "PA", "PM", "SA", "QE", "RTO"}
    assert "PR" not in codes
    assert "RE" not in codes


def test_pi_is_not_reintroduced_without_grounded_construction_authority_reason():
    """Guard against silently reintroducing PI (2026-08-14, confirmed
    category error): "PI" in this codebase's authority-role context
    named Principal Investigator, an academic research role -- not a
    construction site gate like PE/QP/PA/PM/SA. This is a different,
    stronger claim than "unconfirmed" (the PA/PM/SA/QE/RTO posture
    above): PI wasn't awaiting confirmation, it was confirmed wrong.

    If this assertion ever fails because "PI" was added back to
    AuthorityRoleType, that addition needs its own fresh, grounded
    construction-authority justification in a code comment -- not a
    silent revival of the old (mistaken) member, and not an assumption
    that this test is simply stale."""
    codes = {member.value for member in AuthorityRoleType}

    assert "PI" not in codes


def test_discipline_has_exactly_the_three_named_examples():
    """CIVIL/STRUCTURAL/ELECTRICAL are the three disciplines the
    2026-08-14 handoff named as representative examples ("civil,
    structural, electrical, etc.") -- not a claimed-complete taxonomy.
    This guard exists so a future edit doesn't quietly invent a fourth
    discipline without a confirmed source, same posture as
    AuthorityRoleType's unconfirmed-label guard above."""
    disciplines = {member.value for member in Discipline}

    assert disciplines == {"CIVIL", "STRUCTURAL", "ELECTRICAL"}
