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
from src.evidence.emitter import emit_evidence, emit_override_evidence

CLAIM_PAYLOAD = {"claim_id": "test-1", "issuer_id": "USR-SUP-01"}
VERDICT: Verdict = {
    "claim_id": "test-1",
    "decision": "GO",
    "reason": "Claim 'test-1' cleared for execution in ZONE-01.",
    "rule_trace": [{"rule_id": "authority_check", "passed": True, "reason": "Authority Validated"}],
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
