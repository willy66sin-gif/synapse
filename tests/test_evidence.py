"""
Cryptographic hashing & audit output tests.

Must cover: signature is valid SHA-256, payload is deterministic for
identical input, tampering with payload invalidates the signature.
"""
import hashlib
import json

from src.core.evaluator import Verdict
from src.evidence.emitter import emit_evidence

CLAIM_PAYLOAD = {"claim_id": "test-1", "issuer_id": "USR-SUP-01"}
VERDICT: Verdict = {
    "claim_id": "test-1",
    "decision": "GO",
    "reason": "Claim 'test-1' cleared for execution in ZONE-01.",
    "rule_trace": [{"rule_id": "authority_check", "passed": True, "reason": "Authority Validated"}],
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
