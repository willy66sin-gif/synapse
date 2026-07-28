"""
Synapse — FastAPI entrypoint.

Wires together the three pipeline stages defined in CLAUDE.md:
Airlock (ingestion) -> Core (adjudication) -> Evidence (audit emission).

No business logic belongs in this file. Route registration only.
"""
from fastapi import FastAPI

from src.airlock.router import router as airlock_router
from src.supervisor.router import router as supervisor_router

app = FastAPI(title="Synapse", version="0.1.0")

app.include_router(airlock_router)
app.include_router(supervisor_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
