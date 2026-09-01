"""
Scheduled-trigger process for the Hamilton Labs billing statement
(2026-09-01).

Deliberately NOT wired into src/main.py's FastAPI app (no
@app.on_event("startup") background task): several existing tests
(e.g. tests/test_airlock.py, tests/test_supervisor_router.py) run
`with TestClient(app) as client:`, which fires real startup events —
an always-on background asyncio task opening real database connections
on every such test would risk flaky/slow tests and dangling tasks at
teardown, for a concern (wall-clock billing cadence) that has nothing
to do with request handling. Kept as its own standalone process
instead, the same pattern src/core/init_db.py already establishes for
"real infra work that isn't a request handler" (see its own docstring:
"Not imported by src/main.py or any other app module, so it has zero
effect on the FastAPI app's lifespan or on the test suite").

Run via `python -m src.billing.scheduler` (see docker-compose.yml's
`billing-scheduler` service, which runs this alongside the `app`
container). Not imported by src/main.py, so the test suite never
touches this file's actual loop.

poll_interval_seconds is how often the loop wakes up to check whether
a period is due — NOT the billing cadence itself. The cadence
(settings.billing_statement_cadence_days, from src/config.py, fully
configurable via env var) governs is_period_due(); the poll interval
just governs how promptly a due period gets noticed after it elapses.
"""
import asyncio

from src.billing.service import run_scheduled_check
from src.config import settings
from src.core.repository import new_session

DEFAULT_POLL_INTERVAL_SECONDS = 3600


async def run_forever(poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS) -> None:
    while True:
        try:
            async with new_session() as session:
                result = await run_scheduled_check(session, settings)
            if result is not None:
                print(f"Billing statement attempted: delivered={result['delivered']} detail={result['detail']}")
        except Exception as exc:  # noqa: BLE001 - deliberately broad: a background loop must survive a transient failure (DB down, etc.) and retry next tick, not crash the process; same boundary-broad-except precedent as src/core/init_db.py's create_schema()
            print(f"Billing scheduler check failed: {exc}")

        await asyncio.sleep(poll_interval_seconds)


if __name__ == "__main__":
    asyncio.run(run_forever())
