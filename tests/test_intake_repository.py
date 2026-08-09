"""
Identity crosswalk lookup tests (src/intake/repository.py).

No live database -- same stub-session convention as
tests/test_core_repository.py -- but the stub here actually filters by
the statement's bound parameters (via stmt.compile().params) rather
than returning a single canned value regardless of query shape. That
extra fidelity is needed specifically to prove the "wrong type" case:
that resolve_issuer and resolve_zone genuinely discriminate by
external_id_type and don't leak a row across types just because
source_system/external_id happen to match.

Every fixture row uses source_system="TEST_FIXTURE" -- an obviously
fake source name that cannot be mistaken for real integration data,
per the "no real or placeholder-real mapping data" constraint on this
feature. Nothing here is written to a live database; it exists only
for the duration of each test.
"""
import pytest

from src.intake.models import ExternalIdType
from src.intake.repository import resolve_issuer, resolve_zone


class _StubResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeCrosswalkSession:
    """In-memory identity_crosswalk fixture. Rows are (source_system, external_id, external_id_type, synapse_id)."""

    def __init__(self, rows):
        self._rows = rows

    async def execute(self, stmt):
        bound_values = set(stmt.compile().params.values())
        for source_system, external_id, id_type, synapse_id in self._rows:
            if {source_system, external_id, id_type} <= bound_values:
                return _StubResult(synapse_id)
        return _StubResult(None)


@pytest.mark.asyncio
async def test_resolve_zone_returns_synapse_id_for_a_matching_row():
    session = _FakeCrosswalkSession([("TEST_FIXTURE", "EXT-ZONE-1", ExternalIdType.ZONE, "ZONE-01")])

    result = await resolve_zone(session, "TEST_FIXTURE", "EXT-ZONE-1")

    assert result == "ZONE-01"


@pytest.mark.asyncio
async def test_resolve_issuer_returns_synapse_id_for_a_matching_row():
    session = _FakeCrosswalkSession([("TEST_FIXTURE", "EXT-USR-1", ExternalIdType.ISSUER, "USR-SUP-01")])

    result = await resolve_issuer(session, "TEST_FIXTURE", "EXT-USR-1")

    assert result == "USR-SUP-01"


@pytest.mark.asyncio
async def test_resolve_zone_returns_none_when_no_row_matches():
    session = _FakeCrosswalkSession([])

    result = await resolve_zone(session, "TEST_FIXTURE", "EXT-ZONE-UNKNOWN")

    assert result is None


@pytest.mark.asyncio
async def test_resolve_issuer_returns_none_when_no_row_matches():
    session = _FakeCrosswalkSession([])

    result = await resolve_issuer(session, "TEST_FIXTURE", "EXT-USR-UNKNOWN")

    assert result is None


@pytest.mark.asyncio
async def test_resolve_zone_does_not_return_an_issuer_row_with_the_same_external_id():
    """
    Same source_system + external_id, but registered as an issuer, not
    a zone -- resolve_zone must not return it. Proves external_id_type
    is genuinely part of the lookup key, not decorative.
    """
    session = _FakeCrosswalkSession([("TEST_FIXTURE", "EXT-SHARED-1", ExternalIdType.ISSUER, "USR-SUP-01")])

    result = await resolve_zone(session, "TEST_FIXTURE", "EXT-SHARED-1")

    assert result is None


@pytest.mark.asyncio
async def test_resolve_issuer_does_not_return_a_zone_row_with_the_same_external_id():
    session = _FakeCrosswalkSession([("TEST_FIXTURE", "EXT-SHARED-1", ExternalIdType.ZONE, "ZONE-01")])

    result = await resolve_issuer(session, "TEST_FIXTURE", "EXT-SHARED-1")

    assert result is None


@pytest.mark.asyncio
async def test_resolve_zone_is_scoped_to_source_system():
    """A row registered under a different source_system must not leak into another system's lookup."""
    session = _FakeCrosswalkSession([("OTHER_SOURCE", "EXT-ZONE-1", ExternalIdType.ZONE, "ZONE-01")])

    result = await resolve_zone(session, "TEST_FIXTURE", "EXT-ZONE-1")

    assert result is None
