"""
SQLAlchemy ORM models for the rule registry.

Per CLAUDE.md: PostgreSQL is the rule registry / authority record
store. `AuthorizedIssuer` replaces synapse_mdm.py's hardcoded
AUTHORIZED_ISSUERS dict with a real, queryable table.
"""
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class AuthorizedIssuer(Base):
    __tablename__ = "authorized_issuers"

    issuer_id: Mapped[str] = mapped_column(primary_key=True)
    role: Mapped[str]
    clearance_level: Mapped[int]
