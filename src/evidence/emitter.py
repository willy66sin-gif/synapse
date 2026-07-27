"""
Immutable audit-trail logging.

Every adjudication verdict gets emitted here as a signed, append-only
record. Format per CLAUDE.md: JSON-LD payload, SHA-256 signature.

Ported from synapse_mdm.py's `emit_evidence` method: same @context /
type / claim_id / evaluated_at / input_payload / sha256_signature
envelope, adapted to carry our Verdict shape (decision + reason +
rule_trace) instead of a bare (Verdict, reason) tuple. Storage backend
(Postgres write, file, etc.) is not wired up here — this function only
produces the signed record; persisting it is a caller concern.
"""
import hashlib
import json
from datetime import datetime, timezone

from src.core.evaluator import Verdict


def emit_evidence(claim_payload: dict, verdict: Verdict) -> dict:
    """
    Wraps an adjudication verdict in a hashed, timestamped, JSON-LD
    envelope — structurally matching synapse_mdm.py's `emit_evidence`.
    """
    record = {
        "@context": "https://synapse.org/schemas/audit/v1",
        "type": "AdjudicationRecord",
        "claim_id": verdict["claim_id"],
        "decision": verdict["decision"],
        "reason": verdict["reason"],
        "rule_trace": verdict["rule_trace"],
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "input_payload": claim_payload,
    }

    serialized = json.dumps(record, sort_keys=True).encode("utf-8")
    record["sha256_signature"] = hashlib.sha256(serialized).hexdigest()

    return record
