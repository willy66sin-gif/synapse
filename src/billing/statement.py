"""
Statement-of-accounts generator (Hamilton Labs billing, 2026-09-01).

Pure function, no I/O -- same "already-fetched records in, structured
result out" discipline as src/core/evaluator.py's adjudicate() and
src/core/profile_resolution.py's resolve_effective_parameters().
Callers (src/billing/service.py) fetch the AdjudicationRecord dicts for
a period via src/evidence/repository.py's
fetch_adjudication_records_in_range() and pass the resulting list in
here -- this function never touches a database or the network.

See src/billing/schemas.py's own docstring for why this reports
outcome counts only, not a dollar/ROI figure: no such data exists
anywhere in this codebase to consolidate.
"""
from datetime import datetime, timezone
from typing import Optional

from src.billing.schemas import BillingStatement


def generate_statement(
    records: list[dict], period_start: datetime, period_end: datetime, recipient: Optional[str]
) -> BillingStatement:
    """
    records: persisted AdjudicationRecord dicts -- the same shape
    src/evidence/emitter.py's emit_evidence() produces and
    src/evidence/repository.py persists, each carrying at least
    "decision" ("GO"/"NO_GO") and "reason_code" (str or None).
    """
    go_count = sum(1 for record in records if record["decision"] == "GO")
    no_go_count = sum(1 for record in records if record["decision"] == "NO_GO")
    claims_processed = len(records)

    breakdown: dict[str, int] = {}
    for record in records:
        if record["decision"] == "NO_GO":
            code = record.get("reason_code") or "UNSPECIFIED"
            breakdown[code] = breakdown.get(code, 0) + 1

    no_go_rate = (no_go_count / claims_processed) if claims_processed else None

    return BillingStatement(
        period_start=period_start,
        period_end=period_end,
        recipient=recipient,
        claims_processed=claims_processed,
        go_count=go_count,
        no_go_count=no_go_count,
        no_go_rate=no_go_rate,
        no_go_breakdown_by_reason_code=breakdown,
        generated_at=datetime.now(timezone.utc),
    )
