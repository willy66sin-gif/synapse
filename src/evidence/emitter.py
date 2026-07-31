"""
Immutable audit-trail logging.

Every adjudication verdict gets emitted here as a signed, append-only
record. Format per CLAUDE.md: JSON-LD payload, SHA-256 signature.

Ported from synapse_mdm.py's `emit_evidence` method: same @context /
type / claim_id / evaluated_at / input_payload / sha256_signature
envelope, adapted to carry our Verdict shape (decision + reason +
reason_code + rule_trace) instead of a bare (Verdict, reason) tuple.
reason_code (R-DOMAIN-NN, e.g. "R-PTW-01" — see src/core/rules.py) is
None for GO verdicts and for any future failure class that hasn't been
given a code yet; it is not a substitute for `reason`, which stays the
full human-readable message. Storage backend (Postgres write, file,
etc.) is not wired up here — this function only produces the signed
record; persisting it is a caller concern.

authority_binding_id (2026-07-31, Escalation Ownership Principle) is
the resolved src/maestro/directory.py AuthorityBinding.binding_id for
this claim's escalation owner, None on GO. It's a plain optional
string parameter, not an import from src.maestro — Evidence stays
decoupled from Maestro exactly as it already stays decoupled from
src.supervisor, same "pass plain data, not types" pattern
emit_override_evidence() already uses. The caller (src/airlock/router.py)
resolves it before calling this function, since it must be part of the
signed record, not appended after signing.

emit_override_evidence() signs a distinct record type for admin
overrides (src/supervisor/), using the same mechanism. It is purely
additive: it has no way to modify, replace, or remove an existing
AdjudicationRecord — the two record types coexist in the audit trail,
linked only by claim_id.
"""
import hashlib
import json
from datetime import datetime, timezone
from typing import Optional

from src.core.evaluator import Verdict


def emit_evidence(claim_payload: dict, verdict: Verdict, authority_binding_id: Optional[str] = None) -> dict:
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
        "reason_code": verdict["reason_code"],
        "authority_binding_id": authority_binding_id,
        "rule_trace": verdict["rule_trace"],
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "input_payload": claim_payload,
    }

    serialized = json.dumps(record, sort_keys=True).encode("utf-8")
    record["sha256_signature"] = hashlib.sha256(serialized).hexdigest()

    return record


def emit_override_evidence(override: dict) -> dict:
    """
    Signs an admin-override event as its own distinct record type,
    via the same SHA-256/JSON-LD mechanism as emit_evidence(). Takes a
    plain dict (the validated src/supervisor/schemas.py OverrideRecord,
    dumped) rather than importing that type directly, so this module
    stays decoupled from src/supervisor/ exactly as it already stays
    decoupled from src/maestro/.
    """
    record = {
        "@context": "https://synapse.org/schemas/audit/v1",
        "type": "OverrideRecord",
        "claim_id": override["claim_id"],
        "issuer_id": override["issuer_id"],
        "justification": override["justification"],
        "override_timestamp": override["timestamp"],
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }

    serialized = json.dumps(record, sort_keys=True).encode("utf-8")
    record["sha256_signature"] = hashlib.sha256(serialized).hexdigest()

    return record
