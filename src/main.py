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
"""
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.airlock.router import router as airlock_router
from src.supervisor.router import router as supervisor_router

app = FastAPI(title="Synapse", version="0.1.0")

app.include_router(airlock_router)
app.include_router(supervisor_router)
app.mount("/static", StaticFiles(directory="frontend"), name="static")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
