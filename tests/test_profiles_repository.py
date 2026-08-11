"""
Certified profile lookup tests (src/profiles/repository.py).

No live database -- same stub-session convention as
tests/test_telemetry_repository.py (canned row regardless of statement
shape; there's only one lookup key here, profile_id).
"""
import pytest

from src.profiles.models import CertifiedProfileRecord
from src.profiles.repository import fetch_certified_profile
from src.profiles.schemas import ProfileLineage


class _StubResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _StubSession:
    def __init__(self, value):
        self._value = value

    async def execute(self, _stmt):
        return _StubResult(self._value)


@pytest.mark.asyncio
async def test_fetch_certified_profile_returns_none_for_an_unregistered_profile():
    session = _StubSession(None)

    result = await fetch_certified_profile(session, "SG-BC-2024")

    assert result is None


@pytest.mark.asyncio
async def test_fetch_certified_profile_returns_a_standalone_profile():
    row = CertifiedProfileRecord(
        profile_id="SG-BC-2024",
        jurisdiction_code="SG",
        version="2024",
        lineage=ProfileLineage.STANDALONE,
        base_profile_id=None,
        base_profile_version=None,
        parameters={"max_span_m": 12.0},
    )
    session = _StubSession(row)

    result = await fetch_certified_profile(session, "SG-BC-2024")

    assert result.lineage == ProfileLineage.STANDALONE
    assert result.base_ref is None
    assert result.parameters == {"max_span_m": 12.0}


@pytest.mark.asyncio
async def test_fetch_certified_profile_returns_a_base_annex_profile_with_base_ref():
    row = CertifiedProfileRecord(
        profile_id="DE-EC2-ANNEX",
        jurisdiction_code="DE",
        version="2024",
        lineage=ProfileLineage.BASE_ANNEX,
        base_profile_id="EUROCODE-EC2-1-1",
        base_profile_version="2004+A1:2014",
        parameters={"partial_safety_factor": 1.35},
    )
    session = _StubSession(row)

    result = await fetch_certified_profile(session, "DE-EC2-ANNEX")

    assert result.base_ref.base_profile_id == "EUROCODE-EC2-1-1"
    assert result.base_ref.base_profile_version == "2004+A1:2014"
