"""
Synapse — FastAPI entrypoint.

Wires together the three pipeline stages defined in CLAUDE.md:
Airlock (ingestion) -> Core (adjudication) -> Evidence (audit emission).

No business logic belongs in this file. Route registration only.

/static (2026-07-31): mounts frontend/ so src/supervisor/router.py's
GET /supervisor/blocked/{claim_id} can serve
frontend/blocked-screen/blocked-screen.js to the browser. Not a build
step or bundler — frontend/ stays plain, dependency-free JS served
as-is; see frontend/README.md's still-open frontend-stack decision.

src/frontline/ (2026-08-05): first implementation of the Frontline
Worker persona, separate from src/supervisor/ — see
src/frontline/router.py's own doc comment and CLAUDE.md's Escalation
vs. Override — Decoupling Principle.
"""
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.airlock.router import router as airlock_router
from src.doctrine.router import router as doctrine_router
from src.frontline.router import router as frontline_router
from src.supervisor.router import router as supervisor_router

app = FastAPI(title="Synapse", version="0.1.0")

app.include_router(airlock_router)
app.include_router(supervisor_router)
app.include_router(frontline_router)
app.include_router(doctrine_router)
app.mount("/static", StaticFiles(directory="frontend"), name="static")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
