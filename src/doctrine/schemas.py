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
"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict


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
