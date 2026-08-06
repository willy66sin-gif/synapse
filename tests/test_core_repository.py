"""
src/core/repository.py: fetch_issuer_roles() tests (2026-08-06,
IssuerRole migration).

No dedicated test file previously existed for src/core/repository.py's
fetch functions (fetch_issuer_record/fetch_zone_record are exercised
only indirectly, through router-level tests or live Docker infra) --
this is the first. Same stub-session approach as
tests/test_supervisor_router.py, extended to support .scalars().all()
since fetch_issuer_roles returns a list, not a single row.
"""
import pytest

from src.core.models import IssuerRole
from src.core.repository import fetch_issuer_roles
from src.core.roles import AuthorityRoleType


class _StubScalars:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class _StubResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return _StubScalars(self._values)


class _StubSession:
    """Returns a canned list of role_type values, regardless of the
    statement's exact shape -- this stub only ever backs
    fetch_issuer_roles() in these tests, unlike test_supervisor_*.py's
    multi-table stubs."""

    def __init__(self, values):
        self._values = values

    async def execute(self, stmt):
        return _StubResult(self._values)


@pytest.mark.asyncio
async def test_fetch_issuer_roles_returns_every_role_on_file():
    """An issuer may hold multiple independently-accredited roles --
    this is the real-world case that corrected the original
    single-role design (see CLAUDE.md's IssuerRole migration entry)."""
    stub = _StubSession([AuthorityRoleType.PE, AuthorityRoleType.QP])

    roles = await fetch_issuer_roles(stub, "USR-ENG-01")

    assert roles == [AuthorityRoleType.PE, AuthorityRoleType.QP]


@pytest.mark.asyncio
async def test_fetch_issuer_roles_returns_empty_list_for_unknown_issuer():
    """No roles on file is a valid, non-error state -- distinct from
    AuthorizedIssuer not existing at all (that's fetch_issuer_record's
    None case, unaffected by this migration)."""
    stub = _StubSession([])

    roles = await fetch_issuer_roles(stub, "USR-UNKNOWN")

    assert roles == []


def test_issuer_role_model_is_a_row_per_issuer_and_role_type():
    """Schema shape check: IssuerRole is a join table, not a single
    column on AuthorizedIssuer -- one row per (issuer_id, role_type)
    pair, so the same issuer_id can appear in multiple rows, each
    independently representing one accredited role."""
    first = IssuerRole(issuer_id="USR-ENG-01", role_type=AuthorityRoleType.PE)
    second = IssuerRole(issuer_id="USR-ENG-01", role_type=AuthorityRoleType.QP)

    assert first.issuer_id == second.issuer_id
    assert first.role_type != second.role_type


def test_authority_role_type_canonical_import_is_core_roles():
    """AuthorityRoleType now lives in src.core.roles (2026-08-06) --
    src.maestro.directory re-exports the same object for backward
    compatibility, not a separate/duplicate enum."""
    from src.core.roles import AuthorityRoleType as CoreAuthorityRoleType
    from src.maestro.directory import AuthorityRoleType as MaestroAuthorityRoleType

    assert CoreAuthorityRoleType is MaestroAuthorityRoleType
