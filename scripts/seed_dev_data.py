"""
Optional dev-only seed data. NOT run automatically by Docker,
docker-compose, or any app startup hook — deliberately kept separate
from schema provisioning (src/core/init_db.py), per CLAUDE.md's Locked
Design Principles (Schema Provisioning Principle: no seed data ships
automatically).

Inserts one AuthorizedIssuer row (Postgres) and one zone record
(Redis) so a fresh dev environment has something real to submit a
claim against, without hand-crafting SQL/redis-cli commands — this is
what was done by hand during the 2026-07-28 Docker verification pass;
this script just makes that repeatable instead of manual.

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
from src.core.models import AuthorizedIssuer

SEED_ISSUER_ID = "USR-SUP-01"
SEED_ZONE_ID = "ZONE-01"


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
