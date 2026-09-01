"""
Dual-trigger orchestration tests (src/billing/service.py, Hamilton
Labs billing, 2026-09-01).

Covers: is_period_due()'s pure due-check, both trigger entrypoints
(on_claim_finalized() = event-triggered, run_scheduled_check() =
scheduled), and that every send attempt -- success or failure -- gets
its own persisted BillingStatementRecord. Never sends a real email:
src/billing/email_sender.send_statement_email is monkeypatched per
test, same "mock the SMTP call" instruction as
tests/test_billing_email_sender.py.

Fake session mirrors tests/test_profiles_repository.py's/
tests/test_airlock_profile.py's existing stub-session convention
(entity-dispatch execute(), tracked add()/commit()) rather than a live
database.
"""
from datetime import datetime, timedelta, timezone

import pytest

from src.billing import service as billing_service
from src.billing.email_sender import BillingConfigIncompleteError, EmailDeliveryResult
from src.billing.models import BillingStatementAuditEntry
from src.config import Settings
from src.evidence.models import AdjudicationAuditEntry

NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _settings(**overrides):
    base = dict(
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_username="synapse-billing",
        smtp_password="secret",
        smtp_use_tls=True,
        billing_statement_sender="billing@synapse.example.com",
        billing_statement_recipient="ops@hamiltonlabs.example.com",
        billing_statement_cadence_days=30,
    )
    base.update(overrides)
    return Settings(**base)


class _Result:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def scalar_one_or_none(self):
        return self._row

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    """Entity-dispatch stub serving AdjudicationAuditEntry and
    BillingStatementAuditEntry queries -- tracks every add()/commit()
    so tests can assert exactly what evidence got persisted."""

    def __init__(self, adjudication_records=None, last_billing_record_row=None):
        self._adjudication_records = adjudication_records or []
        self._last_billing_record_row = last_billing_record_row
        self.added = []
        self.committed = 0

    async def execute(self, stmt):
        entity = stmt.column_descriptions[0]["entity"]
        if entity is AdjudicationAuditEntry:
            rows = [_Row(record) for record in self._adjudication_records]
            return _Result(rows=rows)
        if entity is BillingStatementAuditEntry:
            return _Result(row=self._last_billing_record_row)
        raise AssertionError(f"unexpected query in test_billing_service.py stub: {stmt}")

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed += 1


class _Row:
    def __init__(self, record):
        self.record = record


def _adjudication_record(claim_id, decision, reason_code=None, evaluated_at=None):
    return {
        "claim_id": claim_id,
        "decision": decision,
        "reason_code": reason_code,
        "evaluated_at": (evaluated_at or NOW).isoformat(),
    }


def _billing_row(period_end_iso, delivered=True):
    return BillingStatementAuditEntry(
        period_start="2026-07-01T00:00:00+00:00",
        period_end=period_end_iso,
        delivered=delivered,
        record={
            "statement": {"period_start": "2026-07-01T00:00:00+00:00", "period_end": period_end_iso},
            "delivered": delivered,
            "detail": "prior run",
        },
    )


# --- is_period_due(): pure decision ---


def test_never_billed_before_is_always_due():
    assert billing_service.is_period_due(None, NOW, 30) is True


def test_not_due_before_cadence_elapses():
    last_period_end = NOW - timedelta(days=10)
    assert billing_service.is_period_due(last_period_end, NOW, 30) is False


def test_due_exactly_at_cadence_boundary():
    last_period_end = NOW - timedelta(days=30)
    assert billing_service.is_period_due(last_period_end, NOW, 30) is True


def test_due_well_past_cadence():
    last_period_end = NOW - timedelta(days=45)
    assert billing_service.is_period_due(last_period_end, NOW, 30) is True


# --- generate_and_send_if_due(): not due yet ---


@pytest.mark.asyncio
async def test_not_due_returns_none_and_persists_nothing():
    session = _FakeSession(last_billing_record_row=_billing_row((NOW - timedelta(days=5)).isoformat()))
    settings = _settings()

    result = await billing_service.generate_and_send_if_due(session, settings, now=NOW)

    assert result is None
    assert session.added == []
    assert session.committed == 0


# --- Event-triggered path: on_claim_finalized() ---


@pytest.mark.asyncio
async def test_event_triggered_send_success_persists_delivered_evidence(monkeypatch):
    def _fake_send(statement, settings):
        return EmailDeliveryResult(delivered=True, detail="Sent to ops@hamiltonlabs.example.com.")

    monkeypatch.setattr(billing_service, "send_statement_email", _fake_send)
    records = [
        _adjudication_record("CLM-1", "GO"),
        _adjudication_record("CLM-2", "NO_GO", "R-PTW-01"),
    ]
    session = _FakeSession(adjudication_records=records, last_billing_record_row=None)
    settings = _settings()

    evidence = await billing_service.on_claim_finalized(session, settings)

    assert evidence is not None
    assert evidence["type"] == "BillingStatementRecord"
    assert evidence["delivered"] is True
    assert evidence["statement"]["claims_processed"] == 2
    assert evidence["statement"]["go_count"] == 1
    assert evidence["statement"]["no_go_count"] == 1
    assert "sha256_signature" in evidence

    assert len(session.added) == 1
    persisted = session.added[0]
    assert isinstance(persisted, BillingStatementAuditEntry)
    assert persisted.delivered is True
    assert session.committed == 1


@pytest.mark.asyncio
async def test_event_triggered_smtp_failure_still_persists_failed_evidence(monkeypatch):
    def _fake_send(statement, settings):
        return EmailDeliveryResult(delivered=False, detail="SMTP send failed: relay refused the message")

    monkeypatch.setattr(billing_service, "send_statement_email", _fake_send)
    session = _FakeSession(adjudication_records=[_adjudication_record("CLM-1", "GO")])
    settings = _settings()

    evidence = await billing_service.on_claim_finalized(session, settings)

    assert evidence["delivered"] is False
    assert "relay refused" in evidence["detail"]
    assert len(session.added) == 1
    assert session.added[0].delivered is False
    assert session.committed == 1


@pytest.mark.asyncio
async def test_event_triggered_missing_config_is_recorded_not_silently_skipped(monkeypatch):
    """Fail-closed config check: send_statement_email() raises
    BillingConfigIncompleteError -- generate_and_send_if_due() must
    still record a failed-send evidence entry, never silently drop the
    attempt from the audit trail."""

    def _raising_send(statement, settings):
        raise BillingConfigIncompleteError("Cannot send billing statement: missing required config value(s): smtp_host.")

    monkeypatch.setattr(billing_service, "send_statement_email", _raising_send)
    session = _FakeSession(adjudication_records=[_adjudication_record("CLM-1", "GO")])
    settings = _settings(smtp_host=None)

    evidence = await billing_service.on_claim_finalized(session, settings)

    assert evidence["delivered"] is False
    assert "missing required config" in evidence["detail"]
    assert len(session.added) == 1
    assert session.added[0].delivered is False
    assert session.committed == 1


@pytest.mark.asyncio
async def test_event_triggered_not_due_does_not_touch_email_sender_at_all(monkeypatch):
    calls = []

    def _tracking_send(statement, settings):
        calls.append(statement)
        return EmailDeliveryResult(delivered=True, detail="should not be called")

    monkeypatch.setattr(billing_service, "send_statement_email", _tracking_send)
    session = _FakeSession(last_billing_record_row=_billing_row((NOW - timedelta(days=1)).isoformat()))
    settings = _settings()

    result = await billing_service.on_claim_finalized(session, settings)

    assert result is None
    assert calls == []


# --- Scheduled path: run_scheduled_check() ---


@pytest.mark.asyncio
async def test_scheduled_trigger_uses_the_same_due_check_and_pipeline(monkeypatch):
    def _fake_send(statement, settings):
        return EmailDeliveryResult(delivered=True, detail="Sent.")

    monkeypatch.setattr(billing_service, "send_statement_email", _fake_send)
    session = _FakeSession(adjudication_records=[_adjudication_record("CLM-1", "NO_GO", "R-ZONE-01")])
    settings = _settings()

    evidence = await billing_service.run_scheduled_check(session, settings)

    assert evidence["delivered"] is True
    assert evidence["statement"]["no_go_breakdown_by_reason_code"] == {"R-ZONE-01": 1}


@pytest.mark.asyncio
async def test_scheduled_trigger_not_due_returns_none():
    session = _FakeSession(last_billing_record_row=_billing_row((NOW - timedelta(days=2)).isoformat()))
    settings = _settings()

    result = await billing_service.run_scheduled_check(session, settings)

    assert result is None


# --- Period boundary correctness ---


@pytest.mark.asyncio
async def test_period_start_is_the_last_billed_periods_end(monkeypatch):
    def _fake_send(statement, settings):
        return EmailDeliveryResult(delivered=True, detail="Sent.")

    monkeypatch.setattr(billing_service, "send_statement_email", _fake_send)
    last_end = NOW - timedelta(days=30)
    session = _FakeSession(
        adjudication_records=[], last_billing_record_row=_billing_row(last_end.isoformat())
    )
    settings = _settings()

    evidence = await billing_service.generate_and_send_if_due(session, settings, now=NOW)

    assert datetime.fromisoformat(evidence["statement"]["period_start"]) == last_end
    assert datetime.fromisoformat(evidence["statement"]["period_end"]) == NOW
