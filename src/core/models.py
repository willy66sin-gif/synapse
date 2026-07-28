"""
SQLAlchemy ORM models for the rule registry.

Per CLAUDE.md: PostgreSQL is the rule registry / authority record
store. `AuthorizedIssuer` replaces synapse_mdm.py's hardcoded
AUTHORIZED_ISSUERS dict with a real, queryable table.

`Base` (the declarative base) is defined here and shared by
src/evidence/models.py and src/supervisor/models.py too, so a single
Base.metadata.create_all() (src/core/init_db.py) provisions every
table across the app in one pass. Those modules importing Base is an
infrastructure-only dependency on Core (the shared ORM base class),
not a logic dependency — Core still has zero knowledge of Evidence,
Maestro, or Supervisor.
"""
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class AuthorizedIssuer(Base):
    __tablename__ = "authorized_issuers"

    issuer_id: Mapped[str] = mapped_column(primary_key=True)
    role: Mapped[str]
    clearance_level: Mapped[int]
