"""
Statement-of-accounts data shape (Hamilton Labs billing, 2026-09-01).

Existence-scan finding, load-bearing for this whole module (do not
re-litigate without re-checking the repo): no cost/price/amount/
currency/ROI field exists anywhere in this codebase's data model.
src/airlock/schemas.py's ClaimPayload.payload_data is explicitly
documented as opaque, rule-irrelevant operational detail (truck_id,
weight_tons in the one example that exists anywhere); nothing in
src/core/, src/evidence/, or src/profiles/ carries a monetary or
ROI-shaped field either. Grepping cost|amount|price|currency|dollar|roi
across src/, tests/, scripts/ turns up zero real hits.

The only real, traceable claim-outcome signal anywhere is
src/evidence/models.py's AdjudicationAuditEntry.decision (GO/NO_GO)
plus its record's reason_code -- see src/billing/statement.py's
generate_statement(), which consolidates exactly that and nothing
else. Per this pass's own instruction ("do not invent fields that
don't trace to real data") and Willy's explicit choice when asked
directly, this statement reports outcome counts only -- no fabricated
dollar or percentage ROI figure.
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class BillingStatement(BaseModel):
    period_start: datetime
    period_end: datetime
    # Optional, not defaulted to a placeholder string: None means
    # settings.billing_statement_recipient was never configured --
    # src/billing/email_sender.py's fail-closed check catches that
    # before ever dialing out, so a statement can still be generated
    # and recorded (e.g. for audit purposes) even when unconfigured,
    # without this field lying about who it's actually addressed to.
    recipient: Optional[str]
    claims_processed: int
    go_count: int
    no_go_count: int
    # None (not a fabricated 0.0) when claims_processed == 0 -- an
    # empty period has no real rate to report.
    no_go_rate: Optional[float]
    no_go_breakdown_by_reason_code: dict[str, int]
    generated_at: datetime
