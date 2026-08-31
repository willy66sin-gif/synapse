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

IssuerRole (2026-08-06, correcting the original Option-B scope from
the prior handoff): a real precedent -- a named individual currently
in the Synapse review process holds multiple independently-accredited
statutory roles -- confirmed the model must support many roles per
issuer, each independently checkable, not one role per issuer and not
"verified once, trusted for everything." A single role_type column on
AuthorizedIssuer couldn't represent that; a join table (one row per
issuer_id + role_type pair) can, and does so without touching
AuthorizedIssuer.role at all -- see that field's own comment below for
why it's deliberately left alone. This shape doesn't preclude adding
per-role verification later (e.g. a nullable registration_id or
verified column on this table): verification would naturally live per
role-row, not per issuer, so no restructuring would be needed when
that becomes real work — not built now, per instruction (schema/
structure only, no fabricated registration data).
"""
from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.core.roles import AuthorityRoleType


class Base(DeclarativeBase):
    pass


class AuthorizedIssuer(Base):
    __tablename__ = "authorized_issuers"

    issuer_id: Mapped[str] = mapped_column(primary_key=True)
    # Free-text, unconstrained, not consulted by src/core/rules.py's
    # check_authority() today (confirmed by inspection — as of commit
    # 2b26af0 it gates on GATE_ADMISSIBLE_ROLES/IssuerRole membership,
    # not this field). Deliberately left untouched by the IssuerRole
    # migration below: the two values that exist anywhere in this repo
    # today ("SUPERINTENDENT", "SITE_ENGINEER") are generic job
    # titles, not verified licensure — neither cleanly maps to
    # AuthorityRoleType, and forcing one would fabricate a credential
    # this repo has no basis for. See CLAUDE.md's Open Items.
    role: Mapped[str]
    # DEPRECATED (2026-08-28, Legacy authority_level/clearance_level
    # discovery pass): unread by check_authority() since commit
    # 2b26af0 -- superseded by GATE_ADMISSIBLE_ROLES/IssuerRole
    # membership. Kept on this table, not removed/migrated out, for
    # symmetry with ClaimPayload.authority_level (src/airlock/schemas.py)
    # -- both went dead for the same reason on the same commit; actual
    # removal is a separate, future decision (would need a real
    # migration, since this column already has live seeded rows).
    clearance_level: Mapped[int]


class IssuerRole(Base):
    """
    One row per (issuer_id, role_type). An issuer may hold zero, one,
    or several roles simultaneously, each its own row — there is no
    single "the" role for an issuer here, unlike AuthorizedIssuer.role
    above.

    Stale-comment correction (2026-08-31, Minimum Viable Local
    Packaging pass): this docstring previously said "not yet populated
    with any real data and not yet read by src/core/rules.py or
    src/core/evaluator.py" — no longer true as of the 2026-08-27
    Authority Admissibility handoff (commit 2b26af0 onward).
    src/core/repository.py's fetch_issuer_roles() reads this table, and
    src/core/rules.py's check_authority()/check_zone_safety()/
    verify_ptw_precondition() all gate admissibility on membership here
    via GATE_ADMISSIBLE_ROLES — see that dict's own doc comment.
    scripts/seed_dev_data.py seeds real rows here for the dev/demo
    issuer as of this pass, for exactly this reason: without at least
    one admissible role, that issuer fails Rule 1 on every claim.
    """

    __tablename__ = "issuer_roles"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    issuer_id: Mapped[str] = mapped_column(ForeignKey("authorized_issuers.issuer_id"), index=True)
    role_type: Mapped[AuthorityRoleType] = mapped_column(SAEnum(AuthorityRoleType))
