"""
/regulatory/instruments route tests (Step 1, 2026-10-11).

Same stub-session infrastructure as tests/test_doctrine_router.py: a
fake AsyncSession that records add()/commit() calls and simulates the
table's uniqueness constraints with the real
sqlalchemy.exc.IntegrityError, so these tests exercise the real FastAPI
routes without needing Postgres. For the GET routes the stub answers
execute() from a canned row list and records each statement, so a test
can check which filters the route actually applied.

Fixture values are invented placeholders, not a real board or
instrument.
"""
import hashlib
import json
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from src.core.repository import get_db_session
from src.doctrine.schemas import GroundingCategory
from src.main import app
from src.regulatory.models import RegulatoryInstrumentRecord

VALID_INSTRUMENT = {
    "instrument_id": "INST-0001",
    "issuing_authority": "EXAMPLE-BOARD",
    "reference_number": "EXAMPLE/REF/2026/001",
    "publish_date": "2026-09-01",
    "effective_date": "2026-10-01",
    "grounding_category": "STATUTE",
}


def _stored_row(instrument_id="INST-0001", reference_number="EXAMPLE/REF/2026/001"):
    return RegulatoryInstrumentRecord(
        instrument_id=instrument_id,
        issuing_authority="EXAMPLE-BOARD",
        reference_number=reference_number,
        publish_date=date(2026, 9, 1),
        effective_date=date(2026, 10, 1),
        grounding_category=GroundingCategory.STATUTE,
        receipt_timestamp=datetime(2026, 9, 2, 9, 0, 0, tzinfo=timezone.utc),
    )


class _StubResult:
    def __init__(self, rows):
        self._rows = rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _StubSession:
    def __init__(self, rows=None):
        self.added = []
        self.committed = []
        self.commit_count = 0
        self.rollback_count = 0
        self.statements = []
        self._rows = rows or []
        self._pending = []
        self._existing_ids = set()
        self._existing_references = set()

    def add(self, obj):
        self.added.append(obj)
        self._pending.append(obj)

    async def commit(self):
        for obj in self._pending:
            if isinstance(obj, RegulatoryInstrumentRecord) and (
                obj.instrument_id in self._existing_ids
                or (obj.issuing_authority, obj.reference_number) in self._existing_references
            ):
                self._pending = []
                raise IntegrityError(
                    "INSERT INTO regulatory_instruments", {}, Exception("UNIQUE constraint failed")
                )
        for obj in self._pending:
            if isinstance(obj, RegulatoryInstrumentRecord):
                self._existing_ids.add(obj.instrument_id)
                self._existing_references.add((obj.issuing_authority, obj.reference_number))
            self.committed.append(obj)
        self.commit_count += 1
        self._pending = []

    async def rollback(self):
        self.rollback_count += 1
        self._pending = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _StubResult(self._rows)


def _client_with_stub(stub_session):
    async def _override_db_session():
        yield stub_session

    app.dependency_overrides[get_db_session] = _override_db_session
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def test_a_complete_instrument_is_accepted_and_evidenced():
    stub = _StubSession()
    before = datetime.now(timezone.utc)

    with _client_with_stub(stub) as client:
        response = client.post("/regulatory/instruments", json=VALID_INSTRUMENT)

    after = datetime.now(timezone.utc)

    assert response.status_code == 200
    evidence = response.json()

    assert evidence["type"] == "RegulatoryInstrumentReceiptRecord"
    assert evidence["instrument"]["instrument_id"] == "INST-0001"
    assert evidence["instrument"]["reference_number"] == "EXAMPLE/REF/2026/001"
    assert evidence["instrument"]["grounding_category"] == "STATUTE"

    unsigned = {key: value for key, value in evidence.items() if key != "sha256_signature"}
    expected_signature = hashlib.sha256(json.dumps(unsigned, sort_keys=True).encode("utf-8")).hexdigest()
    assert evidence["sha256_signature"] == expected_signature

    # receipt_timestamp is server-set, between the two bounds taken
    # immediately around the request.
    receipt_timestamp = datetime.fromisoformat(evidence["instrument"]["receipt_timestamp"])
    assert before <= receipt_timestamp <= after

    # Both derived day-counts are computed server-side from the three
    # distinct dates: publish, effective, and receipt.
    assert evidence["staleness_days"] == (receipt_timestamp.date() - date(2026, 9, 1)).days
    assert evidence["days_until_effective"] == (date(2026, 10, 1) - receipt_timestamp.date()).days

    assert len(stub.added) == 2
    assert stub.commit_count == 2
    instrument_row, receipt_row = stub.added
    assert instrument_row.__class__.__name__ == "RegulatoryInstrumentRecord"
    assert instrument_row.receipt_timestamp == receipt_timestamp
    assert instrument_row.grounding_category == GroundingCategory.STATUTE
    assert receipt_row.__class__.__name__ == "RegulatoryInstrumentReceiptAuditEntry"
    assert receipt_row.issuing_authority == "EXAMPLE-BOARD"
    assert receipt_row.record == evidence


@pytest.mark.parametrize(
    "body",
    [
        {**VALID_INSTRUMENT, "receipt_timestamp": "2026-09-02T09:00:00Z"},
        {key: value for key, value in VALID_INSTRUMENT.items() if key != "effective_date"},
        {key: value for key, value in VALID_INSTRUMENT.items() if key != "grounding_category"},
        {**VALID_INSTRUMENT, "grounding_category": "CIRCULAR"},
        {**VALID_INSTRUMENT, "reference_number": "  "},
        {**VALID_INSTRUMENT, "project_id": "PROJ-TEST-01"},
        {**VALID_INSTRUMENT, "publish_date": "last Tuesday"},
    ],
)
def test_malformed_instrument_422s_and_persists_nothing(body):
    stub = _StubSession()

    with _client_with_stub(stub) as client:
        response = client.post("/regulatory/instruments", json=body)

    assert response.status_code == 422
    assert stub.added == []
    assert stub.commit_count == 0


def test_duplicate_instrument_id_returns_409_and_persists_no_duplicate():
    stub = _StubSession()

    with _client_with_stub(stub) as client:
        first_response = client.post("/regulatory/instruments", json=VALID_INSTRUMENT)
        second_response = client.post("/regulatory/instruments", json=VALID_INSTRUMENT)

    assert first_response.status_code == 200
    assert second_response.status_code == 409
    assert "INST-0001" in second_response.json()["detail"]

    assert stub.rollback_count == 1
    assert stub.commit_count == 2
    committed_by_type = [obj.__class__.__name__ for obj in stub.committed]
    assert committed_by_type.count("RegulatoryInstrumentRecord") == 1
    assert committed_by_type.count("RegulatoryInstrumentReceiptAuditEntry") == 1


def test_same_reference_under_a_new_instrument_id_returns_409():
    stub = _StubSession()

    with _client_with_stub(stub) as client:
        first_response = client.post("/regulatory/instruments", json=VALID_INSTRUMENT)
        second_response = client.post(
            "/regulatory/instruments", json={**VALID_INSTRUMENT, "instrument_id": "INST-0002"}
        )

    assert first_response.status_code == 200
    assert second_response.status_code == 409
    assert "EXAMPLE/REF/2026/001" in second_response.json()["detail"]
    assert [obj.__class__.__name__ for obj in stub.committed].count("RegulatoryInstrumentRecord") == 1


def test_get_instrument_returns_the_stored_instrument():
    stub = _StubSession(rows=[_stored_row()])

    with _client_with_stub(stub) as client:
        response = client.get("/regulatory/instruments/INST-0001")

    assert response.status_code == 200
    assert response.json() == {
        "instrument_id": "INST-0001",
        "issuing_authority": "EXAMPLE-BOARD",
        "reference_number": "EXAMPLE/REF/2026/001",
        "publish_date": "2026-09-01",
        "effective_date": "2026-10-01",
        "grounding_category": "STATUTE",
        "receipt_timestamp": "2026-09-02T09:00:00+00:00",
    }
    assert list(stub.statements[0].compile().params.values()) == ["INST-0001"]


def test_get_unknown_instrument_returns_404():
    stub = _StubSession(rows=[])

    with _client_with_stub(stub) as client:
        response = client.get("/regulatory/instruments/INST-MISSING")

    assert response.status_code == 404
    assert "INST-MISSING" in response.json()["detail"]


def test_list_instruments_returns_every_stored_instrument_unfiltered():
    stub = _StubSession(rows=[_stored_row("INST-0001"), _stored_row("INST-0002", "EXAMPLE/REF/2026/002")])

    with _client_with_stub(stub) as client:
        response = client.get("/regulatory/instruments")

    assert response.status_code == 200
    assert [item["instrument_id"] for item in response.json()] == ["INST-0001", "INST-0002"]
    assert stub.statements[0].compile().params == {}


def test_list_instruments_returns_an_empty_list_for_an_empty_registry():
    stub = _StubSession(rows=[])

    with _client_with_stub(stub) as client:
        response = client.get("/regulatory/instruments")

    assert response.status_code == 200
    assert response.json() == []


def test_list_instruments_filters_by_reference_number_and_issuing_authority():
    """
    A reference number containing "/" is looked up as a query
    parameter -- the stub can't filter, so this checks the statement
    the route built actually carries both exact-match filters.
    """
    stub = _StubSession(rows=[_stored_row()])

    with _client_with_stub(stub) as client:
        response = client.get(
            "/regulatory/instruments",
            params={"issuing_authority": "EXAMPLE-BOARD", "reference_number": "EXAMPLE/REF/2026/001"},
        )

    assert response.status_code == 200
    assert sorted(stub.statements[0].compile().params.values()) == ["EXAMPLE-BOARD", "EXAMPLE/REF/2026/001"]
