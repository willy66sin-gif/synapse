# Synapse

A constitutional adjudication engine. It ingests a structured claim, deterministically
adjudicates it against authority and zone-safety rules, and emits a signed, immutable
audit record — with a channel-agnostic delivery layer (Maestro) for pushing the result
out to WhatsApp/Telegram/etc.

The full architecture, coding standards, and locked design decisions live in
[`CLAUDE.md`](CLAUDE.md) — read that first. In particular:

- **`## Locked Design Principles`** — decisions that are settled and must not be
  silently violated by new code (e.g. no GO/NO-GO surface may show a bare verdict
  without the rule and failing condition; every channel supports both inbound query
  and outbound push; every alert states an escalation contact).
- **`## Open Items`** — known gaps and pending proposals that are *not* locked yet
  (currently: the admin-override execution mechanism).

This README covers day-to-day operator concerns: running it, testing it, and where
things stand. It doesn't restate the architecture — `CLAUDE.md` is authoritative for
that.

## Pipeline

```
Airlock (ingestion, fail-closed schema validation)
  -> Core (pure, stateless adjudication: authority check, zone-safety check)
    -> Evidence (SHA-256-signed, JSON-LD audit record)
```

`Maestro` (`src/maestro/`) is a separate, fully decoupled delivery layer that can turn
an Evidence record into a channel-specific outbound alert (WhatsApp/Telegram stub
adapters included). **As of this writing it is not wired into the live HTTP pipeline**
— `src/airlock/router.py` does not call it. It exists as tested, standalone code; a
real `POST /airlock/claims` today produces a signed evidence record but does not
trigger any delivery.

## Running it

### Option A: Docker (per `docker-compose.yml`)

```bash
docker-compose up
```

This is the intended way to run the full stack (app + PostgreSQL + Redis) together.
**Not yet verified on this development machine** — Docker isn't installed here. If
you have Docker available, this is untested but should work as written; report back
if it doesn't.

### Option B: Non-Docker local dev (verified working)

Requires Python 3.11+.

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux

pip install -r requirements.txt

uvicorn src.main:app --reload
```

This gets you a running app with:
- `GET /health` → `{"status": "ok"}` (no external dependencies)
- `GET /docs` → interactive OpenAPI docs

**What it does *not* get you**: `POST /airlock/claims` requires a live PostgreSQL
(for issuer/authority records) and Redis (for zone state) reachable at the URLs in
`src/config.py` (`DATABASE_URL` / `REDIS_URL` env vars, or an `.env` file). Without
either Docker or natively-installed Postgres/Redis, submitting a claim through the
HTTP endpoint fails loudly with a connection error (by design — see `CLAUDE.md`'s
Fail-Closed Doctrine; there is no silent fallback).

The adjudication logic itself does **not** require a database to test or reason
about — see below.

## Running tests

```bash
pip install -r requirements.txt
pytest
```

No Docker, no database, no `.env` file, and no other undocumented setup required —
`src/core/`'s adjudication logic is pure and stateless by design (`CLAUDE.md`'s
Developer Directives), so the suite runs entirely offline. Verified clean from a
fresh clone, in reversed file order, and across multiple random seeds (36/36 passing
every time, no ordering dependencies).

`pytest.ini` sets `pythonpath = .` — this is required because `tests/` has no
`__init__.py`; without it, `from src...` imports fail when pytest is run from the
project root.

## Repository layout

See `CLAUDE.md`'s `## Repository Layout` section — kept in sync with the actual
`src/` tree as the source of truth.

## Status

36/36 tests passing on `master`. Known open items (see `CLAUDE.md`'s `## Open Items`
for full detail): Docker path unverified on this machine; admin-override execution
mechanism is proposed but not implemented.
