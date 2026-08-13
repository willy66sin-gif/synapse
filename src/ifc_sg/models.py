"""
IFC+SG submission element registry model.

Container only: this table intentionally ships with zero rows, same
discipline as src/doctrine/models.py's DoctrineSubmissionRecord and
src/profiles/models.py's CertifiedProfileRecord. No real element type
-> SGPset_ field mapping is seeded here -- see src/ifc_sg/schemas.py's
module docstring for why (the SGPset_ field catalogue this would need
to be populated from doesn't exist in this repo yet, and inventing one
would be exactly the "absence of data is not compliance" failure this
container exists to avoid repeating).

required_pset_fields stored as JSON (a plain list of strings), not a
join table: mirrors src/doctrine/models.py's citations column -- no
structured Pset/property shape exists yet to normalize against (see
src/ifc_sg/schemas.py's module docstring).

Shares src/core/models.py's Base so src/core/init_db.py's single
create_all() call provisions this table too, same pattern as
src/evidence/models.py, src/supervisor/models.py, src/intake/models.py,
src/telemetry/models.py, src/profiles/models.py, and
src/doctrine/models.py.
"""
from sqlalchemy import JSON
from sqlalchemy.orm import Mapped, mapped_column

from src.core.models import Base


class SubmissionElementSpecRecord(Base):
    __tablename__ = "ifc_sg_submission_elements"

    element_spec_id: Mapped[str] = mapped_column(primary_key=True)
    element_type: Mapped[str]
    jurisdiction_code: Mapped[str]
    required_pset_fields: Mapped[list] = mapped_column(JSON)
