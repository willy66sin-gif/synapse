"""
fetch_adjudication_records_in_range() tests (src/evidence/repository.py),
including its optional project_id filter (2026-09-15, Ring-Fencing
Concept Note -- Willy-authorized implementation).

No live Postgres -- a minimal fake session serving the one
select(AdjudicationAuditEntry).order_by(...).scalars().all() shape this
function issues.
"""
from datetime import datetime, timezone

import pytest

from src.evidence.models import AdjudicationAuditEntry
from src.evidence.repository import fetch_adjudication_records_in_range

PERIOD_START_DT = datetime(2026, 8, 1, tzinfo=timezone.utc)
PERIOD_END_DT = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _row(claim_id, project_id, evaluated_at="2026-08-15T00:00:00+00:00"):
    record = {
        "claim_id": claim_id,
        "decision": "GO",
        "reason_code": None,
        "evaluated_at": evaluated_at,
        "input_payload": {"claim_id": claim_id, "project_id": project_id},
    }
    return AdjudicationAuditEntry(claim_id=claim_id, decision="GO", record=record)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _StubSession:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, _stmt):
        return _Result(self._rows)


@pytest.mark.asyncio
async def test_no_project_id_filter_returns_every_project_unfiltered():
    """Existing, pre-project-scoping behavior must still work unchanged
    when project_id is omitted -- see this pass's own handoff report for
    why the parameter defaults to None (unfiltered) rather than being
    made required outright."""
    session = _StubSession([_row("CLM-A", "PROJ-A"), _row("CLM-B", "PROJ-B")])

    records = await fetch_adjudication_records_in_range(session, PERIOD_START_DT, PERIOD_END_DT)

    assert {r["claim_id"] for r in records} == {"CLM-A", "CLM-B"}


@pytest.mark.asyncio
async def test_project_id_filter_excludes_another_projects_records():
    session = _StubSession([_row("CLM-A", "PROJ-A"), _row("CLM-B", "PROJ-B")])

    records = await fetch_adjudication_records_in_range(session, PERIOD_START_DT, PERIOD_END_DT, project_id="PROJ-A")

    assert {r["claim_id"] for r in records} == {"CLM-A"}


@pytest.mark.asyncio
async def test_project_id_filter_with_no_matching_records_returns_empty():
    session = _StubSession([_row("CLM-A", "PROJ-A")])

    records = await fetch_adjudication_records_in_range(session, PERIOD_START_DT, PERIOD_END_DT, project_id="PROJ-C")

    assert records == []


@pytest.mark.asyncio
async def test_project_id_filter_still_respects_the_date_range():
    """project_id filtering must compose with the existing date-range
    filter, not replace it -- a record in the right project but outside
    the period is still excluded."""
    session = _StubSession(
        [
            _row("CLM-IN-RANGE", "PROJ-A", evaluated_at="2026-08-15T00:00:00+00:00"),
            _row("CLM-OUT-OF-RANGE", "PROJ-A", evaluated_at="2026-06-01T00:00:00+00:00"),
        ]
    )

    records = await fetch_adjudication_records_in_range(session, PERIOD_START_DT, PERIOD_END_DT, project_id="PROJ-A")

    assert {r["claim_id"] for r in records} == {"CLM-IN-RANGE"}
