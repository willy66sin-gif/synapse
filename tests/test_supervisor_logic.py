"""
evaluate_override() tests.

Mirrors tests/test_adjudication.py's approach: IssuerRecord and the
original evidence record are dependency-injected as plain
fixtures/None, so this is a fast, synchronous unit test with no
database or evidence-store dependency, exactly like Core's tests.
"""
from src.core.rules import IssuerRecord
from src.supervisor.logic import evaluate_override
from src.supervisor.schemas import OverrideRecord

SUPERINTENDENT = IssuerRecord(role="SUPERINTENDENT", clearance_level=3)

ORIGINAL_NO_GO_RECORD = {
    "@context": "https://synapse.org/schemas/audit/v1",
    "type": "AdjudicationRecord",
    "claim_id": "CLM-102",
    "decision": "NO_GO",
    "reason": "Safety Violation: Heavy lift requested in high-hazard zone 'ZONE-02'.",
    "rule_trace": [],
    "evaluated_at": "2026-07-28T10:05:00+00:00",
    "input_payload": {},
    "sha256_signature": "irrelevant-for-this-test",
}


def _override(**overrides) -> OverrideRecord:
    base = {
        "claim_id": "CLM-102",
        "issuer_id": "USR-SUP-01",
        "justification": "Zone re-inspected and cleared by site safety officer.",
        "timestamp": "2026-07-28T12:00:00Z",
    }
    base.update(overrides)
    return OverrideRecord(**base)


def test_valid_override_is_accepted():
    outcome = evaluate_override(
        _override(),
        issuer_record=SUPERINTENDENT,
        original_record=ORIGINAL_NO_GO_RECORD,
    )

    assert outcome.accepted is True


def test_unauthenticated_issuer_is_rejected():
    """Fail-closed: an unknown issuer cannot authorize an override."""
    outcome = evaluate_override(
        _override(issuer_id="USR-UNKNOWN"),
        issuer_record=None,
        original_record=ORIGINAL_NO_GO_RECORD,
    )

    assert outcome.accepted is False
    assert "Authority Failure" in outcome.reason


def test_nonexistent_claim_is_rejected():
    """Fail-closed: cannot override a claim that was never adjudicated."""
    outcome = evaluate_override(
        _override(claim_id="CLM-DOES-NOT-EXIST"),
        issuer_record=SUPERINTENDENT,
        original_record=None,
    )

    assert outcome.accepted is False
    assert "Override Rejected" in outcome.reason
    assert "CLM-DOES-NOT-EXIST" in outcome.reason


def test_evaluate_override_is_deterministic():
    override = _override()

    first = evaluate_override(override, issuer_record=SUPERINTENDENT, original_record=ORIGINAL_NO_GO_RECORD)
    second = evaluate_override(override, issuer_record=SUPERINTENDENT, original_record=ORIGINAL_NO_GO_RECORD)

    assert first == second
