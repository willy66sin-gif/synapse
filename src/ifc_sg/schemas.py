"""
IFC+SG submission element schema -- the shape of a single BIM element
type's regulatory property-set requirement within a CORENET X
submission.

Container only, same discipline as src/doctrine/schemas.py's
DoctrineSubmission: this describes which SGPset_ field names an
element type (e.g. door, wall, space) is expected to carry, not
whether any real IFC/IFC+SG file's element actually carries them.
Parsing or validating a real IFC file against this shape is explicitly
out of scope.

Kept separate from src/profiles/schemas.py's CertifiedProfile rather
than folded into it: CertifiedProfile.parameters holds a jurisdiction's
adopted code *values* (flat name -> value pairs, with STANDALONE/
BASE_ANNEX lineage governing how a jurisdiction's values relate to a
shared base code). This schema instead describes the *structural
shape* an element type must have -- which field names are expected,
not what any of them are worth -- and it has no lineage/base-code
concept: an SGPset_ field-name requirement isn't a code parameter one
jurisdiction annexes from another. jurisdiction_code is repeated here
as a plain tag (same pattern src/doctrine/schemas.py already uses
alongside src/profiles/schemas.py) rather than a foreign key into
CertifiedProfile, since a submission element spec is not itself a
certified code profile.

required_pset_fields is a bare list of strings, not a structured
Pset/property model: no complete SGPset_ field catalogue per element
type exists yet to normalize against -- populating that list is a data
problem, not a schema problem -- so a flat list carries exactly as
much structure as is known today, same posture src/doctrine/schemas.py's
citations field takes toward an undecided taxonomy.
"""
from pydantic import BaseModel, ConfigDict


class SubmissionElementSpec(BaseModel):
    """
    The expected SGPset_ shape for one element type in one
    jurisdiction -- not a decision about any real element's compliance.
    """

    model_config = ConfigDict(extra="forbid")

    element_spec_id: str
    element_type: str
    jurisdiction_code: str
    required_pset_fields: list[str]
