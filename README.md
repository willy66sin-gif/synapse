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
  (e.g. binding the authority set to real PEB/MOM/IES/ACES registration data, the
  Frontline Worker's telemetry/assurance-failure target-state stories, and several
  smaller not-yet-decided schema questions — see that section for the full, current
  list rather than this README repeating a single stale example).

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
adapters included). `src/airlock/router.py` calls it on every **NO_GO** adjudication
(any failure class) — a rejected claim always fires a Maestro alert naming the
resolved escalation authority. A **GO** adjudication deliberately triggers no alert;
that asymmetry is intentional (see `CLAUDE.md`'s NO_GO Notification Principle), not
missing wiring. Supervisor override is retired (`POST /supervisor/override` now
returns `410 Gone` unconditionally — see `CLAUDE.md`'s Supervisor Override
Retirement), so it no longer triggers anything.

Both the adjudication and the override are now genuinely persisted (append-only) in
PostgreSQL, not just emitted and forgotten — see `CLAUDE.md`'s Admin-Override
Evidence Principle.

### Admin override — `POST /supervisor/override` (RETIRED)

Retired entirely, not scoped down — see `CLAUDE.md`'s Supervisor Override Retirement.
The endpoint now returns `410 Gone` unconditionally (a structurally malformed body
still 422s at the schema boundary first, unrelated to retirement). A verdict now
changes only via a new, verifiable claim submitted by the specific authority role
that owns the gate being changed, re-adjudicated fresh through Core — not an
administrative action layered on top of an existing verdict. The underlying
`evaluate_override()`/`OverrideRecord` code is still in the repository, unused by
the retired route, kept per instruction pending a separate future removal decision.

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

**2026-08-31 packaging pass note:** the Docker/Compose files themselves are unchanged
by this pass — only `scripts/seed_dev_data.py` and this README were extended. The
2026-07-28 live verification above predates several schema changes since (new
`src/airlock/`, `src/profiles/` tables among them); those are wired into the same
shared `Base.metadata.create_all()` `init_db.py` already calls, and the new seed
script logic was confirmed against the real `adjudicate()` function directly (see
"Seeing a GO and a NO_GO" below) — but a live `docker compose up --build` was **not**
re-run as part of this specific pass (no Docker daemon available in that session). If
this is the first time you're standing this up, treat the commands above as
believed-correct-by-construction, not re-verified end-to-end since 2026-07-28.

No data is seeded automatically. **Nothing will resolve to `GO` — every claim will
NO_GO with "is unauthenticated" — until you run the seed script once, from the repo
root, with the stack up:**

```bash
python scripts/seed_dev_data.py
```

This is a required step for the demo path below, not an optional convenience — it's
kept as a separate manual script rather than automatic (per `CLAUDE.md`'s Schema
Provisioning Principle), but that doesn't mean skippable.

This inserts one `AuthorizedIssuer` row (`USR-SUP-01`), two `IssuerRole` rows for it
(`RTO`, `SA` — required for that issuer to actually pass Rule 1/Rule 2's admissibility
checks under the current architecture; see the script's own comments), one Redis zone
record (`ZONE-01`, low hazard), and one demo `CertifiedProfileRecord`
(`DEMO-PROFILE-01`, obviously-fake jurisdiction, not a real regulator's code). It's
never run automatically by Docker or the app itself — see `CLAUDE.md`'s Schema
Provisioning Principle for why seed data and schema creation are kept deliberately
separate. Safe to re-run; it skips anything already present.

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

## What you're looking at

Two operator-facing screens sit on top of the pipeline above:

- **Frontline Worker screen** (`GET /frontline/blocked/{claim_id}`) — "Can I proceed?"
  A plain-language GO/NO_GO for the person about to do the work. No rule internals,
  no override mechanics. Renders for both GO and NO_GO.
- **Supervisor screen** (`GET /supervisor/blocked/{claim_id}`) — the full picture for
  whoever's accountable: every evaluated rule (pass/fail), the specific failing
  condition, an escalation contact. Deliberately **NO_GO-only** — `409` on a GO claim,
  not a blank success screen.

Both read the same signed evidence record; neither invents anything the other
doesn't already have. Both screens also expose a `GET .../status` polling endpoint
(`/frontline/blocked/{claim_id}/status`, `/supervisor/blocked/{claim_id}/status`)
that re-runs real adjudication fresh on every call and writes a signed transition
record if the verdict actually flips between polls (GO Freshness Phase 1/1b) — this
is a genuine re-check, not a cached re-render of the original decision.

## Other real modules not shown on either screen

These exist, are wired to a live route, and are covered by the automated suite —
listed here so this README doesn't undersell (or oversell) what's actually built,
per `CLAUDE.md`'s own module-by-module changelog:

- **Telemetry** (`src/telemetry/`, `POST /telemetry/zone-state`) — verifies inbound
  sensor data (an Ed25519-signed payload) genuinely came from a registered device,
  unaltered in transit, then writes it into the same zone-state Redis structure
  `adjudicate()` reads, sensor value taking precedence over a human declaration when
  present. Rejects with `422`/`R-DEV-01` (unregistered device) or `R-DEV-02` (bad
  signature). No device PKI, hardware attestation, or HSM-backed trust exists beyond
  the payload signature itself — see `CLAUDE.md`'s Telemetry Transport Security
  Principle for why that's the locked baseline, not a gap.
- **Certified Profiles** (`src/profiles/`) — an optional, permanent `profile_id` on
  a claim can resolve to a `CertifiedProfile` (jurisdiction, code parameters,
  accountable-architect liability binding). `profile_id_enforcement_enabled`
  defaults **off**; see "Optional: seeing `profile_id` enforcement," below, for the
  demo path. No rule in `src/core/rules.py` reads a profile's code parameters yet —
  resolution is wired, but nothing in adjudication consults it today.
- **Billing** (`src/billing/`) — generates a statement of adjudication outcome
  counts (GO/NO_GO tallies by reason code) on a configurable cadence or on a claim
  event, and emails it via SMTP if fully configured (`SMTP_HOST`/`SMTP_PORT`/etc. in
  `src/config.py` — no defaults, so an unconfigured deployment fails closed rather
  than silently skipping the send). There is no cost, price, or currency field
  anywhere in this codebase's data model — a statement is outcome counts only, never
  a dollar figure. Statements are relationship-level, not per-project, by locked
  design decision (see `CLAUDE.md`'s Changelog, 2026-09-15) — there is no
  client-facing per-project billing. Not part of the interactive demo path above;
  requires real SMTP configuration to actually send anything.
- **Doctrine submissions** (`src/doctrine/`, `POST /doctrine/submissions`) — Track B,
  Step 1 only: accepts and persists a project doctrine submission (including Tier 2
  CORENET X parallel-entry fields), signs its own evidence record, and returns `409`
  on a duplicate `submission_id`. There is no grounding-check or review-workflow
  logic yet ("Step 2," not built) — this is a records intake, not an approval
  pipeline.
- **`src/ifc_sg/`** — a zero-row container for the *shape* of a CORENET X submission
  element and its expected `SGPset_` property-set field names. Not a parser, not a
  validator — no real IFC file is ever read by this codebase today. The
  `<gateway-readiness>` frontend component (`frontend/gateway-readiness/`) is a UX
  prototype built against this shape, explicitly labelled "EXAMPLE DATA" throughout
  its demo — it has no backend route and performs no real check.
- **`src/intake/`** — a skeleton adapter for an external ePTW claim source, plus an
  identity-crosswalk container. The `authority_level` crosswalk this would need is
  blocked pending a real external vendor's role vocabulary — see `CLAUDE.md`'s
  2026-08-09 changelog entry. Not wired into the live `/airlock/claims` path.

## Seeing a GO and a NO_GO

**Step 1 — with the stack up, seed it (from the repo root — not `system32`, not any
other directory):**

```bash
python scripts/seed_dev_data.py
```

Skip this and every claim below will NO_GO with `Issuer '...' is unauthenticated` —
not because anything is broken, but because `USR-SUP-01` doesn't exist yet. See
"Troubleshooting" below if you hit that.

**Step 2 — submit these** against the running app (`localhost:8000` under Docker, or
wherever `uvicorn` is bound otherwise):

**GO** — a nominal claim against the seeded zone/issuer:

```bash
curl -X POST localhost:8000/airlock/claims -H "Content-Type: application/json" -d '{
  "claim_id": "CLM-DEMO-GO",
  "timestamp": "2026-08-31T10:00:00Z",
  "project_id": "PROJ-DEMO-01",
  "issuer_id": "USR-SUP-01",
  "authority_level": 3,
  "zone_id": "ZONE-01",
  "action_type": "MATERIAL_ENTRY",
  "payload_data": {},
  "work_type": "NOMINAL_CIVIL"
}'
```

**NO_GO** — high-risk work with no permit-to-work context (fails the ePTW gate,
`R-PTW-01`):

```bash
curl -X POST localhost:8000/airlock/claims -H "Content-Type: application/json" -d '{
  "claim_id": "CLM-DEMO-NOGO",
  "timestamp": "2026-08-31T10:00:00Z",
  "project_id": "PROJ-DEMO-01",
  "issuer_id": "USR-SUP-01",
  "authority_level": 3,
  "zone_id": "ZONE-01",
  "action_type": "EXCAVATION_WORK",
  "payload_data": {},
  "work_type": "EXCAVATION"
}'
```

Both example payloads were run directly against `adjudicate()` while writing this
section to confirm they actually produce the decisions claimed above (`GO` /
`R-PTW-01` respectively) — not just plausible-looking JSON.

Then view either claim on both screens:

```bash
open http://localhost:8000/frontline/blocked/CLM-DEMO-GO      # "You may proceed."
open http://localhost:8000/frontline/blocked/CLM-DEMO-NOGO    # "Do not proceed."
open http://localhost:8000/supervisor/blocked/CLM-DEMO-NOGO   # full rule trace + escalation
# GET /supervisor/blocked/CLM-DEMO-GO returns 409 -- by design, see above.
```

**Troubleshooting.**

- If you see `Issuer '...' is unauthenticated`, you skipped the seed step — run
  `python scripts/seed_dev_data.py` (from the repo root, not `system32` or any other
  directory) and retry. This is the single most common way to hit this: the GO example
  above only works once `USR-SUP-01` actually exists in the database.
- If the seed script itself fails with something like `no such file or directory`,
  you're running it from the wrong directory — `cd` to the repo root (where this
  README lives) first, then run it from there.

**Optional: seeing `profile_id` enforcement.** The seed data includes one demo
`CertifiedProfile` (`DEMO-PROFILE-01`), but `profile_id_enforcement_enabled` defaults
**off** — matching production — so neither example above needs it. If you want to see
the enforcement-on path: add `"profile_id": "DEMO-PROFILE-01"` to a claim payload to
see it resolved and validated even with enforcement off (check the claim's
`rule_trace` for the `profile_check` entry), or set
`PROFILE_ID_ENFORCEMENT_ENABLED=true` in the `app` service's environment (in
`docker-compose.yml`) and restart to see a claim *without* `profile_id` fail closed
with `R-PROFILE-01`. This is optional exploration, not the default demo path.

## What this package does NOT demonstrate

This is a **single trusted-reviewer local instance** — one person, one machine, one
Docker Compose stack. It is explicitly not a production deployment shape, and it does
not demonstrate:

- **Multi-tenancy as an access/authentication boundary.** `project_id` (2026-09-15,
  Ring-Fencing Concept Note) now exists as a required, fail-closed field on every claim
  and Certified Profile — see `CLAUDE.md`'s Changelog — but it is a **data-scoping**
  boundary only: every claim, zone, issuer, and profile still lives in the same single
  Postgres/Redis instance, and there is still no login or per-reviewer account (see the
  next bullet). Project scoping stops a claim from silently referencing another
  project's zone/issuer/profile; it does not add multi-tenant access control.
- **Any external identity or access model.** There is no login, no per-reviewer
  account, no permission boundary between "you" and "the whole database." Anyone with
  the URL and a terminal can do anything any other user of this instance can.
- **Any support or troubleshooting access mechanism.** If something breaks, there is
  no remote-assistance path — you have the same Docker logs and Postgres/Redis access
  as anyone else running this locally.

None of the above is planned for this package. They're real, separate problems for
whenever (if ever) this moves beyond "run it yourself, locally, as a trusted
reviewer."

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

317/317 tests passing on `master` (verified 2026-09-16, HEAD
`53c7b376a1dced228f90a68cd242a4651682d44c`; the 54/54 figure this section previously
stated was current as of 2026-07-28 and had not been kept up since). See
`CLAUDE.md`'s `## Open Items` for the current, full list of known gaps and pending
proposals — the module list above ("Other real modules not shown on either screen")
covers what's built; Open Items covers what's deliberately still undecided.
