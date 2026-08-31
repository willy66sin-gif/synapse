"""
Optional dev-only seed data. NOT run automatically by Docker,
docker-compose, or any app startup hook — deliberately kept separate
from schema provisioning (src/core/init_db.py), per CLAUDE.md's Locked
Design Principles (Schema Provisioning Principle: no seed data ships
automatically).

Inserts:
- One AuthorizedIssuer row (Postgres) — unchanged since the original
  2026-07-28 Docker verification pass.
- Two IssuerRole rows for that issuer, RTO and SA (2026-08-31,
  Minimum Viable Local Packaging pass — a real gap this pass found and
  closed, not a cosmetic addition: since commit 2b26af0,
  check_authority()/check_zone_safety() gate on GATE_ADMISSIBLE_ROLES/
  IssuerRole membership, not AuthorizedIssuer.clearance_level. Without
  at least one admissible role, this seeded issuer fails Rule 1
  (authority_check) on *every* claim regardless of work_type or zone
  state — there was no way to reach a genuine GO against this seed
  data before this addition).
- One zone record (Redis) — unchanged since the original pass.
- One demo CertifiedProfileRecord (2026-08-31 addition, GO Freshness
  Phase 3a follow-on): closes the "CertifiedProfileRecord has zero
  rows anywhere" gap for THIS seeded/demo instance only — the
  production-path default (zero rows) is untouched everywhere else,
  per Phase 3a's own explicit scope. Deliberately an obviously-fake
  jurisdiction/profile ("DEMO"/"DEMO-PROFILE-01"), not a real
  regulator's code — same "no real jurisdiction/regulator data
  fabricated" discipline src/profiles/models.py's own docstring
  already establishes for this table. profile_id_enforcement_enabled
  stays False by default either way (see src/config.py) — this row
  exists so a reviewer *can* opt into seeing enforcement-on behavior
  (see README), not because it's required for the default GO/NO_GO
  demo, which never reads it.

Together this is enough for a real POST /airlock/claims request
against ZONE-01 by USR-SUP-01 to reach a genuine GO — see README.md's
"Seeing a GO and a NO_GO" section for the actual example requests;
this script only seeds state, it deliberately does not submit any
claims itself (a fabricated pre-adjudicated record would misrepresent
something Core never actually decided — the reviewer should trigger
real adjudication through the real endpoint).

Run manually, against a stack that's already up and schema-provisioned
(e.g. `docker-compose up`):

    python scripts/seed_dev_data.py
"""
import asyncio
import sys

sys.path.insert(0, ".")

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.config import settings
from src.core.models import AuthorizedIssuer, IssuerRole
from src.core.roles import AuthorityRoleType
from src.profiles.models import CertifiedProfileRecord
from src.profiles.schemas import ProfileLineage

SEED_ISSUER_ID = "USR-SUP-01"
SEED_ISSUER_ROLES = [AuthorityRoleType.RTO, AuthorityRoleType.SA]
SEED_ZONE_ID = "ZONE-01"
SEED_PROFILE_ID = "DEMO-PROFILE-01"


async def seed_postgres() -> None:
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        existing = await session.execute(
            select(AuthorizedIssuer).where(AuthorizedIssuer.issuer_id == SEED_ISSUER_ID)
        )
        if existing.scalar_one_or_none() is None:
            session.add(AuthorizedIssuer(issuer_id=SEED_ISSUER_ID, role="SUPERINTENDENT", clearance_level=3))
            await session.commit()
            print(f"Seeded AuthorizedIssuer {SEED_ISSUER_ID}.")
        else:
            print(f"AuthorizedIssuer {SEED_ISSUER_ID} already present, skipping.")

        existing_roles = await session.execute(
            select(IssuerRole.role_type).where(IssuerRole.issuer_id == SEED_ISSUER_ID)
        )
        already_held = set(existing_roles.scalars().all())
        for role_type in SEED_ISSUER_ROLES:
            if role_type in already_held:
                print(f"IssuerRole {SEED_ISSUER_ID}/{role_type.value} already present, skipping.")
                continue
            session.add(IssuerRole(issuer_id=SEED_ISSUER_ID, role_type=role_type))
            print(f"Seeded IssuerRole {SEED_ISSUER_ID}/{role_type.value}.")
        await session.commit()

        existing_profile = await session.execute(
            select(CertifiedProfileRecord).where(CertifiedProfileRecord.profile_id == SEED_PROFILE_ID)
        )
        if existing_profile.scalar_one_or_none() is None:
            session.add(
                CertifiedProfileRecord(
                    profile_id=SEED_PROFILE_ID,
                    jurisdiction_code="DEMO",
                    version="0.1-demo",
                    lineage=ProfileLineage.STANDALONE,
                    base_profile_id=None,
                    base_profile_version=None,
                    parameters={},
                )
            )
            await session.commit()
            print(f"Seeded CertifiedProfileRecord {SEED_PROFILE_ID}.")
        else:
            print(f"CertifiedProfileRecord {SEED_PROFILE_ID} already present, skipping.")

    await engine.dispose()


async def seed_redis() -> None:
    redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
    await redis_client.hset(f"zone:{SEED_ZONE_ID}", mapping={"hazard_level": "LOW", "active_crane": "false"})
    print(f"Seeded zone:{SEED_ZONE_ID} in Redis.")
    await redis_client.aclose()


async def main() -> None:
    await seed_postgres()
    await seed_redis()


if __name__ == "__main__":
    asyncio.run(main())
