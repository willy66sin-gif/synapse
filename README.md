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
  (currently: whether ordinary claim adjudications, not just overrides, should also
  push a Maestro alert).

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
adapters included). **`src/airlock/router.py` still does not call it** — an ordinary
`POST /airlock/claims` produces a signed evidence record but triggers no delivery.
An accepted admin override *does* trigger a Maestro alert (see below) — that's the
only live Maestro wiring so far.

Both the adjudication and the override are now genuinely persisted (append-only) in
PostgreSQL, not just emitted and forgotten — see `CLAUDE.md`'s Admin-Override
Evidence Principle.

### Admin override — `POST /supervisor/override`

Accepts `{claim_id, issuer_id, justification, timestamp}`. Fail-closed like Airlock
(422 on malformed input, extra fields forbidden, `justification` can't be empty).
Business-logic rejections use real HTTP status codes rather than always-200 (unlike
`/airlock/claims`, where NO_GO is itself a valid outcome): `403` if the issuer isn't
a known authority, `404` if the claim has no adjudication record to override. On
acceptance: signs a distinct `OverrideRecord` evidence entry, persists it, and sends
a Maestro alert (both stub channels) announcing the override.

## Running it

### Option A: Docker (per `docker-compose.yml`) — verified working

```bash
docker compose up --build
```

Builds and starts all three containers (`app`, `db`, `cache`). Verified 2026-07-28:
clean startup logs for all three, `pg_isready`/`redis-cli ping` healthy in-container,
all three ports (5432/6379/8000) reachable from the host, and a real
`POST /airlock/claims` request against the live stack returns a genuine signed
`GO`/`NO_GO` response — no mocks involved.

Schema is provisioned automatically — `src/core/init_db.py` runs on every container
start (wired into the `Dockerfile`'s `CMD`, before `uvicorn`), calling
`Base.metadata.create_all()` against `src/core/models.py`. A genuinely fresh
`docker compose down -v && docker compose up --build` (empty volumes) produces a
working, queryable schema with zero manual steps — verified 2026-07-28.

No data is seeded automatically. If you want something to submit a claim against
without hand-crafting SQL/redis-cli commands, run the optional, separate dev-seed
script once the stack is up:

```bash
python scripts/seed_dev_data.py
```

This inserts one `AuthorizedIssuer` row and one Redis zone record. It's never run
automatically by Docker or the app itself — see `CLAUDE.md`'s Schema Provisioning
Principle for why seed data and schema creation are kept deliberately separate.

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
fresh clone, in reversed file order, and across multiple random seeds (no ordering
dependencies, at the 36/36-test count that was current at the time).

The new `src/evidence/repository.py` and `src/supervisor/repository.py` I/O
functions are deliberately *not* unit-tested with mocks — consistent with
`src/core/repository.py`'s existing functions, which never had direct unit tests
either. The automated suite covers `POST /supervisor/override`'s branching logic via
a stub session (see `tests/test_supervisor_router.py`); the actual database writes
are verified live, against real Docker containers, the same way `/airlock/claims`
always has been.

`pytest.ini` sets `pythonpath = .` — this is required because `tests/` has no
`__init__.py`; without it, `from src...` imports fail when pytest is run from the
project root.

## Repository layout

See `CLAUDE.md`'s `## Repository Layout` section — kept in sync with the actual
`src/` tree as the source of truth.

## Status

54/54 tests passing on `master`. Known open item (see `CLAUDE.md`'s `## Open Items`
for full detail): whether ordinary `/airlock/claims` adjudications, not just
overrides, should also push a Maestro alert.
