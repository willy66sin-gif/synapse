"""
Cryptographic hashing & audit output tests.

Must cover: signature is valid SHA-256, payload is deterministic for
identical input, tampering with payload invalidates the signature.

Also covers emit_override_evidence() (src/supervisor/'s admin-override
records): same signature guarantees, plus that it is purely additive
— emitting an override never touches the original AdjudicationRecord.
"""
import hashlib
import json

from src.core.evaluator import Verdict
from src.evidence.emitter import (
    emit_billing_statement_evidence,
    emit_doctrine_submission_evidence,
    emit_evidence,
    emit_override_evidence,
    emit_profile_rejection_evidence,
)

CLAIM_PAYLOAD = {"claim_id": "test-1", "issuer_id": "USR-SUP-01"}
VERDICT: Verdict = {
    "claim_id": "test-1",
    "decision": "GO",
    "reason": "Claim 'test-1' cleared for execution in ZONE-01.",
    "rule_trace": [{"rule_id": "authority_check", "passed": True, "reason": "Authority Validated"}],
    "reason_code": None,
}

NO_GO_VERDICT: Verdict = {
    "claim_id": "test-2",
    "decision": "NO_GO",
    "reason": "FAIL_CLOSED_EPTW_PRECONDITION: No permit-to-work context provided for high-risk work_type 'EXCAVATION'.",
    "rule_trace": [{"rule_id": "ptw_precondition_check", "passed": False, "reason": "no permit"}],
    "reason_code": "R-PTW-01",
}

OVERRIDE = {
    "claim_id": "test-1",
    "issuer_id": "USR-SUP-01",
    "justification": "Zone re-inspected and cleared by site safety officer.",
    "timestamp": "2026-07-28T12:00:00Z",
}


def test_emit_evidence_produces_signature():
    result = emit_evidence(CLAIM_PAYLOAD, VERDICT)

    assert "sha256_signature" in result
    assert len(result["sha256_signature"]) == 64  # SHA-256 hex digest length


def test_emit_evidence_signature_matches_recomputed_hash():
    """The signature must actually be the SHA-256 of the rest of the record."""
    result = emit_evidence(CLAIM_PAYLOAD, VERDICT)

    unsigned = {k: v for k, v in result.items() if k != "sha256_signature"}
    expected = hashlib.sha256(json.dumps(unsigned, sort_keys=True).encode("utf-8")).hexdigest()

    assert result["sha256_signature"] == expected


def test_tampering_with_payload_invalidates_signature():
    result = emit_evidence(CLAIM_PAYLOAD, VERDICT)

    tampered = dict(result)
    tampered["decision"] = "NO_GO"  # simulate post-hoc tampering

    unsigned = {k: v for k, v in tampered.items() if k != "sha256_signature"}
    recomputed = hashlib.sha256(json.dumps(unsigned, sort_keys=True).encode("utf-8")).hexdigest()

    assert recomputed != tampered["sha256_signature"]


def test_emit_override_evidence_produces_signature():
    result = emit_override_evidence(OVERRIDE)

    assert "sha256_signature" in result
    assert len(result["sha256_signature"]) == 64


def test_emit_override_evidence_signature_matches_recomputed_hash():
    result = emit_override_evidence(OVERRIDE)

    unsigned = {k: v for k, v in result.items() if k != "sha256_signature"}
    expected = hashlib.sha256(json.dumps(unsigned, sort_keys=True).encode("utf-8")).hexdigest()

    assert result["sha256_signature"] == expected


def test_override_evidence_tampering_invalidates_signature():
    result = emit_override_evidence(OVERRIDE)

    tampered = dict(result)
    tampered["justification"] = "different reason entirely"

    unsigned = {k: v for k, v in tampered.items() if k != "sha256_signature"}
    recomputed = hashlib.sha256(json.dumps(unsigned, sort_keys=True).encode("utf-8")).hexdigest()

    assert recomputed != tampered["sha256_signature"]


def test_override_evidence_is_a_distinct_record_type():
    override_record = emit_override_evidence(OVERRIDE)

    assert override_record["type"] == "OverrideRecord"
    assert override_record["type"] != "AdjudicationRecord"


def test_override_evidence_does_not_mutate_original_adjudication_record():
    """The Admin-Override Evidence Principle: additive only, never a mutation."""
    original = emit_evidence(CLAIM_PAYLOAD, VERDICT)
    original_snapshot = dict(original)

    emit_override_evidence(OVERRIDE)

    assert original == original_snapshot


# --- GO Freshness Phase 3a, Part A: emit_profile_rejection_evidence() ---


def test_emit_profile_rejection_evidence_produces_signature():
    result = emit_profile_rejection_evidence("CLM-501", "SG-BC-2024", "R-PROFILE-02")

    assert "sha256_signature" in result
    assert len(result["sha256_signature"]) == 64


def test_emit_profile_rejection_evidence_signature_matches_recomputed_hash():
    result = emit_profile_rejection_evidence("CLM-501", "SG-BC-2024", "R-PROFILE-02")

    unsigned = {k: v for k, v in result.items() if k != "sha256_signature"}
    expected = hashlib.sha256(json.dumps(unsigned, sort_keys=True).encode("utf-8")).hexdigest()

    assert result["sha256_signature"] == expected


def test_emit_profile_rejection_evidence_is_a_distinct_record_type():
    result = emit_profile_rejection_evidence("CLM-501", "SG-BC-2024", "R-PROFILE-02")

    assert result["type"] == "ProfileRejectionRecord"
    assert result["type"] not in ("AdjudicationRecord", "OverrideRecord")


def test_emit_profile_rejection_evidence_records_null_profile_id_when_missing():
    """R-PROFILE-01 (missing profile_id): profile_id is recorded as None,
    not omitted or coerced to an empty string -- distinguishes "never
    supplied" from "supplied but unresolvable" (R-PROFILE-02) purely by
    this field, same record type either way."""
    result = emit_profile_rejection_evidence("CLM-501", None, "R-PROFILE-01")

    assert result["profile_id"] is None
    assert result["reason_code"] == "R-PROFILE-01"


def test_emit_profile_rejection_evidence_does_not_mutate_original_adjudication_record():
    original = emit_evidence(CLAIM_PAYLOAD, VERDICT)
    original_snapshot = dict(original)

    emit_profile_rejection_evidence("CLM-501", "SG-BC-2024", "R-PROFILE-02")

    assert original == original_snapshot


def test_emit_evidence_carries_none_reason_code_for_go_verdict():
    result = emit_evidence(CLAIM_PAYLOAD, VERDICT)

    assert result["reason_code"] is None


def test_emit_evidence_threads_specific_reason_code_for_no_go_verdict():
    """A PTW-rejected claim's persisted evidence must carry the specific
    failure reason, not just decision="NO_GO" indistinguishable from any
    other rejection."""
    result = emit_evidence(CLAIM_PAYLOAD, NO_GO_VERDICT)

    assert result["decision"] == "NO_GO"
    assert result["reason_code"] == "R-PTW-01"


def test_emit_evidence_reason_code_is_included_in_the_signed_payload():
    """The signature must cover reason_code too — tampering with it must
    invalidate the signature, same guarantee as any other field."""
    result = emit_evidence(CLAIM_PAYLOAD, NO_GO_VERDICT)

    tampered = dict(result)
    tampered["reason_code"] = "R-AUTH-01"

    unsigned = {k: v for k, v in tampered.items() if k != "sha256_signature"}
    recomputed = hashlib.sha256(json.dumps(unsigned, sort_keys=True).encode("utf-8")).hexdigest()

    assert recomputed != tampered["sha256_signature"]


def test_emit_evidence_defaults_authority_binding_id_to_none():
    result = emit_evidence(CLAIM_PAYLOAD, VERDICT)

    assert result["authority_binding_id"] is None


def test_emit_evidence_persists_provided_authority_binding_id():
    """Escalation Ownership Principle: the resolved authority_binding_id
    must be part of the signed evidence record, not deferred to a later
    phase."""
    result = emit_evidence(CLAIM_PAYLOAD, NO_GO_VERDICT, authority_binding_id="BIND-999")

    assert result["authority_binding_id"] == "BIND-999"


# --- Hamilton Labs statement-of-accounts (2026-09-01): emit_billing_statement_evidence() ---

BILLING_STATEMENT = {
    "period_start": "2026-08-01T00:00:00+00:00",
    "period_end": "2026-09-01T00:00:00+00:00",
    "recipient": "ops@hamiltonlabs.example.com",
    "claims_processed": 10,
    "go_count": 8,
    "no_go_count": 2,
    "no_go_rate": 0.2,
    "no_go_breakdown_by_reason_code": {"R-PTW-01": 2},
    "generated_at": "2026-09-01T00:00:05+00:00",
}


def test_emit_billing_statement_evidence_produces_signature():
    result = emit_billing_statement_evidence(BILLING_STATEMENT, delivered=True, detail="Sent.")

    assert "sha256_signature" in result
    assert len(result["sha256_signature"]) == 64


def test_emit_billing_statement_evidence_signature_matches_recomputed_hash():
    result = emit_billing_statement_evidence(BILLING_STATEMENT, delivered=True, detail="Sent.")

    unsigned = {k: v for k, v in result.items() if k != "sha256_signature"}
    expected = hashlib.sha256(json.dumps(unsigned, sort_keys=True).encode("utf-8")).hexdigest()

    assert result["sha256_signature"] == expected


def test_emit_billing_statement_evidence_is_a_distinct_record_type():
    result = emit_billing_statement_evidence(BILLING_STATEMENT, delivered=True, detail="Sent.")

    assert result["type"] == "BillingStatementRecord"
    assert result["type"] not in ("AdjudicationRecord", "OverrideRecord", "ProfileRejectionRecord")


def test_emit_billing_statement_evidence_records_failed_sends_not_just_successes():
    """'every send... including failed-send attempts' -- delivered=False
    must be a real, distinguishable value on the signed record, not
    coerced to True or omitted."""
    result = emit_billing_statement_evidence(
        BILLING_STATEMENT, delivered=False, detail="SMTP send failed: relay refused the message"
    )

    assert result["delivered"] is False
    assert "relay refused" in result["detail"]


def test_emit_billing_statement_evidence_tampering_invalidates_signature():
    result = emit_billing_statement_evidence(BILLING_STATEMENT, delivered=True, detail="Sent.")

    tampered = dict(result)
    tampered["delivered"] = False

    unsigned = {k: v for k, v in tampered.items() if k != "sha256_signature"}
    recomputed = hashlib.sha256(json.dumps(unsigned, sort_keys=True).encode("utf-8")).hexdigest()

    assert recomputed != tampered["sha256_signature"]


# --- Tier 2 CORENET X Parallel Entry (2026-09-02): emit_doctrine_submission_evidence() ---

DOCTRINE_SUBMISSION = {
    "submission_id": "SUB-0001",
    "submitting_party_id": "Acme Architects",
    "jurisdiction_code": "SG",
    "citations": ["SS EN 1992-1-1"],
    "ambiguity_resolution_notes": "n/a",
    "submitted_at": "2026-08-12T09:30:00",
    "signed_off": True,
    "corenet_x_reference": "CNX-2026-00417",
    "corenet_x_gateway": "DESIGN",
    "corenet_x_approval_date": "2026-08-01",
    "entered_by": "QP",
    "receipt_timestamp": "2026-08-06T00:00:00+00:00",
}


def test_emit_doctrine_submission_evidence_produces_signature():
    result = emit_doctrine_submission_evidence(DOCTRINE_SUBMISSION, staleness_days=5)

    assert "sha256_signature" in result
    assert len(result["sha256_signature"]) == 64


def test_emit_doctrine_submission_evidence_signature_matches_recomputed_hash():
    result = emit_doctrine_submission_evidence(DOCTRINE_SUBMISSION, staleness_days=5)

    unsigned = {k: v for k, v in result.items() if k != "sha256_signature"}
    expected = hashlib.sha256(json.dumps(unsigned, sort_keys=True).encode("utf-8")).hexdigest()

    assert result["sha256_signature"] == expected


def test_emit_doctrine_submission_evidence_is_a_distinct_record_type():
    result = emit_doctrine_submission_evidence(DOCTRINE_SUBMISSION, staleness_days=5)

    assert result["type"] == "DoctrineSubmissionReceiptRecord"
    assert result["type"] not in (
        "AdjudicationRecord",
        "OverrideRecord",
        "ProfileRejectionRecord",
        "BillingStatementRecord",
    )


def test_emit_doctrine_submission_evidence_carries_the_full_submission_and_staleness():
    result = emit_doctrine_submission_evidence(DOCTRINE_SUBMISSION, staleness_days=5)

    assert result["submission"] == DOCTRINE_SUBMISSION
    assert result["staleness_days"] == 5


def test_emit_doctrine_submission_evidence_tampering_invalidates_signature():
    result = emit_doctrine_submission_evidence(DOCTRINE_SUBMISSION, staleness_days=5)

    tampered = dict(result)
    tampered["staleness_days"] = 999

    unsigned = {k: v for k, v in tampered.items() if k != "sha256_signature"}
    recomputed = hashlib.sha256(json.dumps(unsigned, sort_keys=True).encode("utf-8")).hexdigest()

    assert recomputed != tampered["sha256_signature"]


def test_emit_doctrine_submission_evidence_does_not_mutate_original_adjudication_record():
    original = emit_evidence(CLAIM_PAYLOAD, VERDICT)
    original_snapshot = dict(original)

    emit_doctrine_submission_evidence(DOCTRINE_SUBMISSION, staleness_days=5)

    assert original == original_snapshot


def test_emit_evidence_authority_binding_id_is_included_in_the_signed_payload():
    """The signature must cover authority_binding_id too — tampering
    with it must invalidate the signature, same guarantee as reason_code."""
    result = emit_evidence(CLAIM_PAYLOAD, NO_GO_VERDICT, authority_binding_id="BIND-999")

    tampered = dict(result)
    tampered["authority_binding_id"] = "BIND-101"

    unsigned = {k: v for k, v in tampered.items() if k != "sha256_signature"}
    recomputed = hashlib.sha256(json.dumps(unsigned, sort_keys=True).encode("utf-8")).hexdigest()

    assert recomputed != tampered["sha256_signature"]
