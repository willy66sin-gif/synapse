"""
Pure override-authorization logic.

Zero I/O — same discipline as src/core/rules.py and
src/core/evaluator.py. evaluate_override() takes already-resolved
data (an IssuerRecord, or None if the issuer is unknown; the original
adjudication evidence record, or None if no such claim was ever
adjudicated) and returns a deterministic outcome. Resolving that data
from PostgreSQL / an evidence store is a caller concern, deliberately
kept out of this module, exactly like src/core/repository.py is kept
separate from src/core/rules.py.

Note: there is currently no persisted evidence store anywhere in this
codebase (src/evidence/emitter.py's docstring already flags "storage
backend not yet wired up"). original_record is dependency-injected as
Optional[dict] so this logic is fully testable now, without that gap
needing to be closed first — see CLAUDE.md's Open Items for what's
still undecided about wiring this to a live endpoint.
"""
from dataclasses import dataclass
from typing import Optional

from src.core.rules import IssuerRecord
from src.supervisor.schemas import OverrideRecord


@dataclass(frozen=True)
class OverrideOutcome:
    accepted: bool
    reason: str


def evaluate_override(
    override: OverrideRecord,
    issuer_record: Optional[IssuerRecord],
    original_record: Optional[dict],
) -> OverrideOutcome:
    """
    Authority Check: the overriding issuer must be a known,
    authenticated authority (same "unauthenticated" failure mode as
    src/core/rules.py's check_authority).

    Existence Check: the claim being overridden must have an actual
    adjudication record on file — an override of a claim_id that was
    never adjudicated is meaningless and must fail closed.
    """
    if issuer_record is None:
        return OverrideOutcome(
            accepted=False,
            reason=f"Authority Failure: Issuer '{override.issuer_id}' is unauthenticated.",
        )

    if original_record is None:
        return OverrideOutcome(
            accepted=False,
            reason=f"Override Rejected: Claim '{override.claim_id}' has no adjudication record on file.",
        )

    return OverrideOutcome(
        accepted=True,
        reason=f"Override accepted for claim '{override.claim_id}' by '{override.issuer_id}'.",
    )
