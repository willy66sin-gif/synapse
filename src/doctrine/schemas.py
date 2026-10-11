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

Doctrine Grounding Standard, Step 0 (2026-10-11, Willy-authorized):
citations is now a list of Citation entries -- each one a
GroundingCategory tag plus the freeform reference text -- replacing the
prior bare list of strings. This is a structural gate only: the schema
boundary verifies every citation is tagged with one of the four
categories and carries non-blank reference text, and that at least one
citation is present. It does NOT verify the tag is the *correct* one
for that reference, and does NOT re-verify the cited body's
accreditation, the statute's existence, or anything else about the
source itself -- that independent check is still the deferred
"grounding check" described above, still not built. reference stays a
plain str for the same reason the old bare-string list did: nothing in
this repo holds a catalogue of real statutes/bodies/cases for it to be
validated against.

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

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


class GroundingCategory(str, Enum):
    """
    The four Doctrine Grounding Standard categories a citation may be
    tagged with (Step 0, 2026-10-11). Exactly the four values named in
    that build's brief -- no fifth/OTHER value is guessed at here, so a
    citation that fits none of them fails closed (422) rather than
    being filed under a catch-all.

    IHL is carried as given: the brief supplied the code only, not its
    expansion, and none is invented here.

    Defined in this module, not src/core/: Core has no knowledge of
    doctrine grounding (same posture as the CORENET X fields below), so
    unlike AuthorityRoleType (src/core/roles.py) there is no
    Core-side consumer that would justify hosting it there.
    src/regulatory/ reuses this same enum rather than declaring a
    second copy.
    """

    STATUTE = "STATUTE"
    ACCREDITED_BODY = "ACCREDITED_BODY"
    PEER_REVIEWED_CASE_LAW = "PEER_REVIEWED_CASE_LAW"
    IHL = "IHL"


class Citation(BaseModel):
    """
    One tagged citation: which grounding category the submitter
    declares it under, and the reference text itself.

    Structural only -- see this module's docstring. `category` is the
    submitter's own declaration, the same standing as `signed_off`
    below: recorded as filed, not checked against anything.
    """

    model_config = ConfigDict(extra="forbid")

    category: GroundingCategory
    reference: str

    @field_validator("reference")
    @classmethod
    def _reference_is_not_blank(cls, value: str) -> str:
        """
        Fail-closed at the schema boundary: a tagged citation with
        empty or whitespace-only reference text is a tag attached to
        nothing -- rejected with 422, not accepted as "present".
        """
        if not value.strip():
            raise ValueError("citation reference must not be blank.")
        return value


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
    # min_length=1 (Step 0, 2026-10-11): "tagged and present" is read
    # here as at least one citation per submission -- a submission
    # citing nothing has no grounding to tag. Flagged back to Willy as
    # an interpretation, not a separately-stated requirement.
    citations: list[Citation] = Field(min_length=1)
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
