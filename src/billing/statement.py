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
    records: list[dict],
    period_start: datetime,
    period_end: datetime,
    recipient: Optional[str],
    project_id: Optional[str] = None,
) -> BillingStatement:
    """
    records: persisted AdjudicationRecord dicts -- the same shape
    src/evidence/emitter.py's emit_evidence() produces and
    src/evidence/repository.py persists, each carrying at least
    "decision" ("GO"/"NO_GO") and "reason_code" (str or None).

    project_id (2026-09-15, Ring-Fencing Concept Note -- Willy-authorized
    implementation; billing scope LOCKED 2026-09-15, see CLAUDE.md's
    Changelog): does not filter `records` itself -- the caller
    (src/evidence/repository.py's fetch_adjudication_records_in_range())
    already did that filtering, if requested, before records reached
    here; this function stays a pure consolidation step with no I/O,
    same discipline as before this pass. Threaded through purely so the
    resulting BillingStatement can record which project (if any) it was
    scoped to -- see BillingStatement.project_id's own comment.

    Optional, defaults None, and that default is the locked, correct
    shape for every real billing statement: Hamilton Labs bills the
    client relationship as a whole, not per project, so a monthly
    statement -- for the client relationship and for Hamilton Labs' own
    internal tracking alike -- is a relationship-level statement,
    unscoped. generate_and_send_if_due() (src/billing/service.py), the
    only caller in the automatic scheduled/event-triggered pipeline,
    always calls this with project_id omitted, by design. A non-None
    project_id exists for Hamilton Labs' own internal analysis/tracking
    outside that automatic pipeline (e.g. an ad hoc per-project
    breakdown), never for client-facing per-project billing -- there is
    no such feature, and this parameter is not what would build one.
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
        project_id=project_id,
        recipient=recipient,
        claims_processed=claims_processed,
        go_count=go_count,
        no_go_count=no_go_count,
        no_go_rate=no_go_rate,
        no_go_breakdown_by_reason_code=breakdown,
        generated_at=datetime.now(timezone.utc),
    )
