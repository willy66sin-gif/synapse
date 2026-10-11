"""
Regulatory instrument schema -- the record of a standing instrument
(circular, advisory letter, or similar) issued by a statutory board,
independent of any project submission (Step 1, 2026-10-11,
Willy-authorized).

Deliberately a separate model and package from src/doctrine/:
DoctrineSubmission is project-bound (its CORENET X gateway fields are
required), so a standing instrument addressed to an industry at large
cannot be filed there. Nothing here carries a project_id at all.

Container only, same discipline as src/doctrine/schemas.py: this is
the shape of an instrument as entered, not a decision about it. No
field here is read by src/core/ -- registering an instrument changes
no adjudication outcome. The one Core rule that cites an instrument
(src/core/rules.py's check_lift_panel_oem_consent()) does so by a
fixed reference number, not by looking a row up in this registry.

Scoped for any statutory board, not one. Where that breadth outruns
what has actually been seen, the field stays loose rather than
guessing a structure:
  - issuing_authority is a plain str, not an enum: no confirmed,
    closed list of boards has been supplied (BCA is the only one with
    a real example behind it), so a guessed enum would reject the
    first real board it didn't anticipate.
  - reference_number is a plain str with no format validation: the
    only real example seen is BCA's "BCA/EMEG/GEN/2026/018"; nothing
    confirms other boards follow that shape.
  - publish_date and effective_date are both required. Whether every
    board's instruments carry a separately-stated effective date is a
    GUESS -- no example exists here either way. An instrument that
    takes effect on publication is entered with the two dates equal.
    No ordering between the two is enforced, for the same reason.

grounding_category reuses src/doctrine/schemas.py's GroundingCategory
rather than declaring a second copy. Like Citation.category there, it
is the entering party's declaration, recorded as filed -- a structural
gate, not a ruling on which category an instrument truly belongs to.

receipt_timestamp follows src/doctrine/schemas.py's pattern exactly:
set server-side by src/regulatory/router.py at creation time, and a
client-supplied value is rejected at the schema boundary (422), not
silently overwritten. It is distinct from both publish_date and
effective_date: those are facts about the instrument, this is when
Synapse was told about it.
"""
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from src.doctrine.schemas import GroundingCategory


class RegulatoryInstrument(BaseModel):
    """
    A standing regulatory instrument, as entered -- not yet checked
    against anything, and not consulted by adjudication.

    instrument_id is the caller-supplied registry key, same convention
    as DoctrineSubmission.submission_id. It exists separately from
    reference_number because a board's own reference contains path
    separators ("BCA/EMEG/GEN/2026/018") and cannot safely be a URL
    path segment for GET /regulatory/instruments/{instrument_id}.
    """

    model_config = ConfigDict(extra="forbid")

    instrument_id: str
    issuing_authority: str
    reference_number: str
    publish_date: date
    effective_date: date
    grounding_category: GroundingCategory
    receipt_timestamp: Optional[datetime] = None

    @field_validator("instrument_id", "issuing_authority", "reference_number")
    @classmethod
    def _identifying_fields_are_not_blank(cls, value: str) -> str:
        """
        Fail-closed at the schema boundary: an instrument with a blank
        key, issuer, or reference identifies nothing -- rejected with
        422, same posture as src/doctrine/schemas.py's Citation.reference.
        """
        if not value.strip():
            raise ValueError("instrument_id, issuing_authority and reference_number must not be blank.")
        return value

    @model_validator(mode="after")
    def _receipt_timestamp_is_not_client_supplied(self) -> "RegulatoryInstrument":
        """
        Same rule, same wording, as DoctrineSubmission's validator of
        the same name: receipt_timestamp is server-set on creation.
        """
        if self.receipt_timestamp is not None:
            raise ValueError(
                "receipt_timestamp is server-set on creation and must not be supplied by the client."
            )
        return self
