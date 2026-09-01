"""
Statement-of-accounts generation correctness (src/billing/statement.py's
generate_statement(), Hamilton Labs billing, 2026-09-01).

Pure function -- no I/O, no fake session needed. Confirms it only ever
consolidates real, traceable fields (decision, reason_code) off the
same AdjudicationRecord shape src/evidence/emitter.py's emit_evidence()
actually produces -- never a fabricated dollar/ROI figure (see
src/billing/schemas.py's own docstring for why).
"""
from datetime import datetime, timezone

from src.billing.statement import generate_statement

PERIOD_START = datetime(2026, 8, 1, tzinfo=timezone.utc)
PERIOD_END = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _record(decision, reason_code=None):
    return {"claim_id": "CLM-1", "decision": decision, "reason_code": reason_code}


def test_empty_period_reports_zero_counts_and_no_rate():
    statement = generate_statement([], PERIOD_START, PERIOD_END, "hamilton-labs@example.com")

    assert statement.claims_processed == 0
    assert statement.go_count == 0
    assert statement.no_go_count == 0
    assert statement.no_go_rate is None  # not a fabricated 0.0
    assert statement.no_go_breakdown_by_reason_code == {}


def test_counts_go_and_no_go_claims_correctly():
    records = [_record("GO"), _record("GO"), _record("NO_GO", "R-PTW-01")]

    statement = generate_statement(records, PERIOD_START, PERIOD_END, "hamilton-labs@example.com")

    assert statement.claims_processed == 3
    assert statement.go_count == 2
    assert statement.no_go_count == 1


def test_no_go_rate_is_a_real_fraction_of_the_period():
    records = [_record("GO"), _record("NO_GO", "R-AUTH-01"), _record("NO_GO", "R-ZONE-01"), _record("GO")]

    statement = generate_statement(records, PERIOD_START, PERIOD_END, "hamilton-labs@example.com")

    assert statement.no_go_rate == 0.5


def test_breakdown_groups_no_go_claims_by_reason_code():
    records = [
        _record("NO_GO", "R-PTW-01"),
        _record("NO_GO", "R-PTW-01"),
        _record("NO_GO", "R-AUTH-02"),
        _record("GO"),
    ]

    statement = generate_statement(records, PERIOD_START, PERIOD_END, "hamilton-labs@example.com")

    assert statement.no_go_breakdown_by_reason_code == {"R-PTW-01": 2, "R-AUTH-02": 1}


def test_breakdown_excludes_go_claims_entirely():
    records = [_record("GO"), _record("GO")]

    statement = generate_statement(records, PERIOD_START, PERIOD_END, "hamilton-labs@example.com")

    assert statement.no_go_breakdown_by_reason_code == {}


def test_null_reason_code_on_a_no_go_is_bucketed_as_unspecified_not_dropped():
    """Real data can have decision=NO_GO with reason_code=None in principle
    (Verdict["reason_code"] is Optional) -- must still be counted, not
    silently excluded from the breakdown."""
    records = [_record("NO_GO", None)]

    statement = generate_statement(records, PERIOD_START, PERIOD_END, "hamilton-labs@example.com")

    assert statement.no_go_count == 1
    assert statement.no_go_breakdown_by_reason_code == {"UNSPECIFIED": 1}


def test_statement_carries_the_period_and_recipient_it_was_asked_for():
    statement = generate_statement([], PERIOD_START, PERIOD_END, "hamilton-labs@example.com")

    assert statement.period_start == PERIOD_START
    assert statement.period_end == PERIOD_END
    assert statement.recipient == "hamilton-labs@example.com"


def test_recipient_none_is_preserved_not_replaced_with_a_placeholder():
    """When billing_statement_recipient isn't configured, the statement
    must say so honestly (None), not invent a placeholder string --
    see src/billing/schemas.py's own docstring."""
    statement = generate_statement([], PERIOD_START, PERIOD_END, None)

    assert statement.recipient is None


def test_statement_does_not_carry_any_dollar_or_roi_field():
    """Guards the core design constraint of this module: no cost/price/
    amount/currency/ROI field exists anywhere in this codebase's data
    model, so the statement must not fabricate one either."""
    statement = generate_statement([_record("GO")], PERIOD_START, PERIOD_END, "hamilton-labs@example.com")

    fields = set(statement.model_dump().keys())
    forbidden = {"roi", "roi_gain", "cost", "amount", "price", "currency", "revenue", "value"}
    assert fields.isdisjoint(forbidden)
