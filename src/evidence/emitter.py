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

authority_binding_id (2026-07-31, Escalation Ownership Principle;
list-valued as of 2026-08-18) is the resolved
src/maestro/directory.py AuthorityBinding.binding_id list for this
claim's escalation owner(s) — one entry from the reason_code tier,
plus QP/QE's two if the claim is a design alteration — None on GO. A
plain optional list-of-strings parameter, not an import from
src.maestro — Evidence stays decoupled from Maestro exactly as it
already stays decoupled from src.supervisor, same "pass plain data,
not types" pattern emit_override_evidence() already uses. The caller
(src/airlock/router.py) resolves it before calling this function,
since it must be part of the signed record, not appended after
signing.

emit_override_evidence() signs a distinct record type for admin
overrides (src/supervisor/), using the same mechanism. It is purely
additive: it has no way to modify, replace, or remove an existing
AdjudicationRecord — the two record types coexist in the audit trail,
linked only by claim_id.

emit_sensor_zone_state_evidence() (2026-08-27, telemetry-ingestion-
pathway build) signs a third, distinct record type for a verified-
telemetry ZoneRecord write (src/telemetry/zone_write.py). Per that
build's evidence-emission decision, this does not share
AdjudicationRecord's or OverrideRecord's evidence trail — it is its
own record type, carrying source="VERIFIED_TELEMETRY" specifically so
a reader can tell this write came from a verified device, not a human
declaration (e.g. scripts/seed_dev_data.py, which writes ZoneRecord
fields directly to Redis with no evidence emission of any kind, before
and after this build alike).

emit_sensor_zone_rejection_evidence() (2026-08-27, telemetry-
rejection-evidence addendum) signs a fourth, distinct record type for
a *rejected* verified-telemetry write attempt (device unregistered or
signature invalid) — no Redis write happened, so this is not a fourth
variant of "a write occurred," it's an audit record of the rejection
itself. Deliberately does NOT reuse source="VERIFIED_TELEMETRY" (that
value asserts the telemetry WAS verified, which is precisely what
didn't happen here) — see this function's own docstring for the value
used instead. reason_code (R-DEV-01/R-DEV-02, from
src/telemetry/trust.py's exception classes) is threaded straight into
the record, mirroring how emit_evidence() threads Verdict.reason_code.
"""
import hashlib
import json
from datetime import datetime, timezone
from typing import Optional

from src.core.evaluator import Verdict


def emit_evidence(
    claim_payload: dict, verdict: Verdict, authority_binding_id: Optional[list[str]] = None
) -> dict:
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


def emit_sensor_zone_state_evidence(device_id: str, zone_id: str, field: str, value: bool) -> dict:
    """
    Signs a verified-telemetry ZoneRecord field write as its own
    distinct record type, via the same SHA-256/JSON-LD mechanism as
    emit_evidence()/emit_override_evidence(). Takes plain values, not
    a DeviceRegistryEntry/ZoneRecord type, so this module stays
    decoupled from src/telemetry/ exactly as it already stays
    decoupled from src/maestro/ and src/supervisor/.

    source="VERIFIED_TELEMETRY" is what lets a reader of the audit
    trail distinguish this write from a human declaration — the
    specific requirement behind keeping this its own evidence-emission
    path rather than folding it into emit_evidence()'s trail.
    """
    record = {
        "@context": "https://synapse.org/schemas/audit/v1",
        "type": "SensorZoneStateRecord",
        "device_id": device_id,
        "zone_id": zone_id,
        "field": field,
        "value": value,
        "source": "VERIFIED_TELEMETRY",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }

    serialized = json.dumps(record, sort_keys=True).encode("utf-8")
    record["sha256_signature"] = hashlib.sha256(serialized).hexdigest()

    return record


def emit_sensor_zone_rejection_evidence(
    device_id: str, zone_id: str, field: str, attempted_value: bool, reason_code: str
) -> dict:
    """
    Signs a rejected verified-telemetry write attempt as its own
    distinct record type, via the same SHA-256/JSON-LD mechanism as
    the other emit_*() functions in this module. Kept separate from
    emit_sensor_zone_state_evidence()'s SensorZoneStateRecord type —
    see this module's docstring for why.

    attempted_value is recorded under its own key, not `value`, to
    make clear it was never verified or written — this is a record of
    what a device *claimed*, not a fact Synapse trusts.

    source="TELEMETRY_REJECTED", not "VERIFIED_TELEMETRY" — the field
    exists on both record types for a consistent "where did this
    attempt originate" read, but the value itself must not claim a
    verification that didn't happen.
    """
    record = {
        "@context": "https://synapse.org/schemas/audit/v1",
        "type": "SensorZoneStateRejectionRecord",
        "device_id": device_id,
        "zone_id": zone_id,
        "field": field,
        "attempted_value": attempted_value,
        "reason_code": reason_code,
        "source": "TELEMETRY_REJECTED",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
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
