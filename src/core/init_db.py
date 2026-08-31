"""
One-off schema provisioning — run once per container start, before
uvicorn, via the Dockerfile's CMD. Not imported by src/main.py or any
other app module, so it has zero effect on the FastAPI app's lifespan
or on the test suite (TestClient never touches this file).

Calls Base.metadata.create_all() against src/core/models.py's shared
declarative Base. create_all() is idempotent (only creates tables
that don't already exist), so this is safe to run on every container
start, not just the first.

SQLAlchemy only registers a table on Base.metadata when its model
class has actually been imported somewhere — so every models.py in
the app (src/core/models.py, src/airlock/models.py, src/doctrine/models.py,
src/evidence/models.py, src/supervisor/models.py, src/intake/models.py,
src/telemetry/models.py, src/profiles/models.py, src/ifc_sg/models.py)
must be imported here, even though none of their classes are
referenced directly below, or their tables silently never get created.

This is schema *creation*, not a migration tool. Deliberate choice for
this project's stage: no production data, no schema-evolution history,
one developer. Alembic would add real overhead (autogenerate
discipline, upgrade/downgrade scripts wired into the deploy path) for
no current benefit. Revisit this once a schema change needs to
preserve existing data, or once create_all()'s create-only semantics
(no ALTERs) become limiting — see CLAUDE.md's Locked Design
Principles.

Retries briefly: docker-compose's `depends_on: [db]` only waits for
the db container's process to start, not for Postgres itself to be
ready to accept connections, so the very first attempt on a fresh
volume can race Postgres's own startup.
"""
import asyncio

from sqlalchemy.ext.asyncio import create_async_engine

from src.airlock import models as _airlock_models  # noqa: F401 - imported for its table-registration side effect
from src.config import settings
from src.core.models import Base
from src.doctrine import models as _doctrine_models  # noqa: F401 - imported for its table-registration side effect
from src.evidence import models as _evidence_models  # noqa: F401 - imported for its table-registration side effect
from src.ifc_sg import models as _ifc_sg_models  # noqa: F401 - imported for its table-registration side effect
from src.intake import models as _intake_models  # noqa: F401 - imported for its table-registration side effect
from src.profiles import models as _profiles_models  # noqa: F401 - imported for its table-registration side effect
from src.supervisor import models as _supervisor_models  # noqa: F401 - imported for its table-registration side effect
from src.telemetry import models as _telemetry_models  # noqa: F401 - imported for its table-registration side effect

MAX_ATTEMPTS = 10
RETRY_DELAY_SECONDS = 2


async def create_schema() -> None:
    engine = create_async_engine(settings.database_url)
    last_error: Exception | None = None

    try:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                async with engine.begin() as conn:
                    await conn.run_sync(Base.metadata.create_all)
                print(f"Schema ready (attempt {attempt}/{MAX_ATTEMPTS}).")
                return
            except Exception as exc:  # noqa: BLE001 - deliberately broad: waiting on a boot-time dependency, not swallowing a request-time failure
                last_error = exc
                print(f"Database not ready yet (attempt {attempt}/{MAX_ATTEMPTS}): {exc}")
                if attempt < MAX_ATTEMPTS:
                    await asyncio.sleep(RETRY_DELAY_SECONDS)
    finally:
        await engine.dispose()

    raise SystemExit(f"Could not create schema after {MAX_ATTEMPTS} attempts: {last_error}")


if __name__ == "__main__":
    asyncio.run(create_schema())
