"""
Synapse Minimum Deterministic Model (synapse_mdm.py)
Authoritative Reference Harness for Execution Governance
"""

import json
import hashlib
from datetime import datetime, timezone
from typing import Dict, Any, Tuple
from enum import Enum


class Verdict(str, Enum):
    CLEARED = "GO"
    BLOCKED = "NO-GO"
    REJECTED = "NO-GO"


class SynapseMDM:
    """
    Deterministic Adjudication Engine (Core).
    HARD CONSTRAINT: Zero internal LLM calls, zero NLP parsing, zero probabilistic logic.
    """

    # Active site state registry (Simulated Redis state cache)
    ACTIVE_ZONES = {
        "ZONE-01": {"hazard_level": "LOW", "active_crane": False},
        "ZONE-02": {"hazard_level": "HIGH", "active_crane": True},
    }

    AUTHORIZED_ISSUERS = {
        "USR-SUP-01": {"role": "SUPERINTENDENT", "clearance_level": 3},
        "USR-ENG-02": {"role": "SITE_ENGINEER", "clearance_level": 1},
    }

    @classmethod
    def validate_schema(cls, payload: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Constitutional Airlock: Strict fail-closed schema validation.
        Rejects missing fields or unstructured prose immediately.
        """
        required_fields = {
            "claim_id": str,
            "timestamp": str,
            "issuer_id": str,
            "authority_level": int,
            "zone_id": str,
            "action_type": str,
            "payload_data": dict,
        }

        for field, expected_type in required_fields.items():
            if field not in payload:
                return False, f"Airlock Reject: Missing required field '{field}'."
            if not isinstance(payload[field], expected_type):
                return False, f"Airlock Reject: Invalid type for '{field}'. Expected {expected_type.__name__}."

        return True, "Schema Validated"

    @classmethod
    def adjudicate(cls, payload: Dict[str, Any]) -> Tuple[Verdict, str]:
        """
        Pure, stateless rule processing engine.
        Evaluates atomic claim against explicit site boundaries and authority rules.
        """
        issuer_id = payload["issuer_id"]
        authority_level = payload["authority_level"]
        zone_id = payload["zone_id"]
        action_type = payload["action_type"]

        # Rule 1: Authority Check
        if issuer_id not in cls.AUTHORIZED_ISSUERS:
            return Verdict.REJECTED, f"Authority Failure: Issuer '{issuer_id}' is unauthenticated."

        user_info = cls.AUTHORIZED_ISSUERS[issuer_id]
        if authority_level < user_info["clearance_level"]:
            return Verdict.REJECTED, f"Authority Failure: Level {authority_level} insufficient for role."

        # Rule 2: Physical Boundary & Zone Safety Check
        if zone_id not in cls.ACTIVE_ZONES:
            return Verdict.BLOCKED, f"Safety Violation: Zone '{zone_id}' does not exist."

        zone_info = cls.ACTIVE_ZONES[zone_id]
        if action_type == "LIFT_OPERATION" and zone_info["hazard_level"] == "HIGH":
            return Verdict.BLOCKED, f"Safety Violation: Heavy lift requested in high-hazard zone '{zone_id}'."

        # Nominal Success
        return Verdict.CLEARED, f"Claim '{payload['claim_id']}' cleared for execution in {zone_id}."

    @classmethod
    def emit_evidence(cls, payload: Dict[str, Any], verdict: Verdict, reason: str) -> Dict[str, Any]:
        """
        Evidence Emitter: Generates immutable, SHA-256 signed JSON-LD execution logs.
        """
        log_entry = {
            "@context": "https://synapse.org/schemas/audit/v1",
            "type": "AdjudicationRecord",
            "claim_id": payload.get("claim_id", "INVALID"),
            "verdict": verdict.value,
            "reason": reason,
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "input_payload": payload,
        }

        # Generate cryptographic SHA-256 signature of the evaluation payload
        serialized = json.dumps(log_entry, sort_keys=True).encode("utf-8")
        signature = hashlib.sha256(serialized).hexdigest()
        log_entry["sha256_signature"] = signature

        return log_entry


def run_tests():
    """Reference Harness Regression Suite"""
    print("=== Running Synapse MDM Reference Test Suite ===")

    # Test Case 1: Nominal
    nominal_payload = {
        "claim_id": "CLM-101",
        "timestamp": "2026-07-27T10:00:00Z",
        "issuer_id": "USR-SUP-01",
        "authority_level": 3,
        "zone_id": "ZONE-01",
        "action_type": "MATERIAL_ENTRY",
        "payload_data": {"truck_id": "SG1234A", "weight_tons": 12.5}
    }

    valid, msg = SynapseMDM.validate_schema(nominal_payload)
    assert valid, f"Airlock failed on nominal payload: {msg}"
    verdict, reason = SynapseMDM.adjudicate(nominal_payload)
    evidence = SynapseMDM.emit_evidence(nominal_payload, verdict, reason)
    print(f"\n[NOMINAL TEST] Verdict: {verdict.value} | Reason: {reason}")
    print(f"SHA-256 Signature: {evidence['sha256_signature']}")
    assert verdict == Verdict.CLEARED

    # Test Case 2: Safety Violation
    safety_payload = {
        "claim_id": "CLM-102",
        "timestamp": "2026-07-27T10:05:00Z",
        "issuer_id": "USR-SUP-01",
        "authority_level": 3,
        "zone_id": "ZONE-02",
        "action_type": "LIFT_OPERATION",
        "payload_data": {"crane_id": "CR-01"}
    }
    verdict, reason = SynapseMDM.adjudicate(safety_payload)
    print(f"\n[SAFETY VIOLATION TEST] Verdict: {verdict.value} | Reason: {reason}")
    assert verdict == Verdict.BLOCKED

    # Test Case 3: Fail-Closed Airlock Handling
    invalid_payload = {"raw_prose": "Hey can we move the truck to Zone 1?"}
    valid, msg = SynapseMDM.validate_schema(invalid_payload)
    print(f"\n[AIRLOCK FAIL-CLOSED TEST] Schema Valid: {valid} | Message: {msg}")
    assert not valid

    print("\n✅ All reference tests passed successfully!")


if __name__ == "__main__":
    run_tests()
