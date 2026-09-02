"""
Doctrine submission schema -- the record an architect/firm files when
proposing a project-specific doctrine for a jurisdiction.

Container only, same discipline as src/profiles/schemas.py's
CertifiedProfile: this is the shape of a submission, not a decision
about it. Whether a submission is grounded in a valid doctrine-standard
category for its jurisdiction -- the "grounding check" -- is explicitly
not this schema's job. That step is deferred, same posture
src/intake/adapters/eptw.py's CrosswalkMissError takes toward
crosswalk data that doesn't exist yet: the mechanism to hold a
submission exists, but nothing here guesses at what "valid" means for
a category that hasn't been decided.

No status/review field (e.g. PENDING/APPROVED/REJECTED): a review
workflow depends on the grounding check that isn't built, so encoding
workflow states here would be exactly the "logic pretending to know
what's not yet decided" this module is scoped to avoid. `signed_off`
below is the submitter's own attestation, not a review outcome --
see this class's docstring.

citations is a bare list of strings, not a structured citation model:
no doctrine-standard category taxonomy exists yet for a citation to be
validated against (that's the grounding check), so there is nothing
for a structured shape to add over freeform text.

CORENET X Parallel Entry, Tier 2 (2026-09-02): adds a parallel,
human-entered record of a project's CORENET X gateway approval
alongside the doctrine submission it accompanies. Per that build's own
non-goals, this is explicitly NOT a CORENET X API integration (no
network call exists anywhere in this module) and does NOT gate
adjudication -- src/core/ has no knowledge this field set exists.
corenet_x_reference/corenet_x_gateway/corenet_x_approval_date are the
human-entered approval facts; entered_by is a plain str carrying an
existing authority-role identifier (e.g. an AuthorityRoleType code --
see src/core/roles.py) -- deliberately typed str, not
AuthorityRoleType, per that build's explicit instruction not to add a
new role concept here. receipt_timestamp is set server-side by
src/doctrine/router.py at creation time; the validator below makes the
schema boundary itself reject a client-supplied value, same fail-closed
posture as this file's other doctrine.
"""
from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, model_validator


class CorenetXGateway(str, Enum):
    """
    The CORENET X project gateway a submission's approval was granted
    at. Four gateways named in the Tier 2 CORENET X Parallel Entry
    brief -- no fifth/other value is guessed at here.
    """

    DESIGN = "DESIGN"
    PILING = "PILING"
    CONSTRUCTION = "CONSTRUCTION"
    COMPLETION = "COMPLETION"


class DoctrineSubmission(BaseModel):
    """
    A submitted project doctrine, as filed -- not yet checked against
    anything.

    `signed_off` is the submitting architect/firm's own attestation
    that the submission is complete and ready for review; it says
    nothing about whether the submission passes the (not yet built)
    grounding check.
    """

    model_config = ConfigDict(extra="forbid")

    submission_id: str
    submitting_party_id: str
    jurisdiction_code: str
    citations: list[str]
    ambiguity_resolution_notes: str
    submitted_at: datetime
    signed_off: bool
    corenet_x_reference: str
    corenet_x_gateway: CorenetXGateway
    corenet_x_approval_date: date
    entered_by: str
    receipt_timestamp: Optional[datetime] = None

    @model_validator(mode="after")
    def _receipt_timestamp_is_not_client_supplied(self) -> "DoctrineSubmission":
        """
        Fail-closed at the schema boundary: receipt_timestamp is
        server-set by src/doctrine/router.py at creation time, from
        datetime.now(timezone.utc) -- a request body that supplies its
        own value is malformed input under this file's fail-closed
        doctrine, rejected here with 422, not silently overwritten.
        """
        if self.receipt_timestamp is not None:
            raise ValueError(
                "receipt_timestamp is server-set on creation and must not be supplied by the client."
            )
        return self
