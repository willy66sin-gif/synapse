# Synapse Architecture & Coding Standards

## System Invariants & Authoritative Boundaries
- System Principle: Core decides, Maestro delivers.
- Authority Model: CLAUDE.md is the immutable constitution. Do not alter architecture, add NLP/prose generation inside Core, or introduce probabilistic logic on your own initiative.
- Pipeline Boundaries: Constitutional Airlock (Ingestion) -> Constitutional Adjudication Engine (Core) -> Evidence Emitter (Audit).
- Fail-Closed Doctrine: Reject unstructured prose or invalid schemas immediately with HTTP 422 / Schema Error. Zero internal NLP/prose parsing inside Core.
- Core Adjudication Constraints: Stateless, pure functions, zero-generation logic executing deterministic rule checks.

## Locked Design Principles
- Supervisor UI Principle: No GO/NO-GO surface may present a standalone verdict. Every decision shown to a user must be paired with the Rule ID and the full set of evaluated sub-conditions (pass/fail per condition); for NO-GO, the specific conflicting condition must be shown explicitly. Applies to every delivery surface (dashboard, WhatsApp, Telegram, future channels).
- Hybrid Surface Design: Every delivery channel supports two directions — inbound (a requester queries the status of a claim) and outbound (Synapse pushes a GO/NO-GO alert unprompted). A channel adapter is incomplete unless it implements both.
- Escalation Requirement: Every rendered GO/NO-GO alert must state how to escalate — who or what contact point to reach for an override — regardless of channel. Maestro and its channel adapters only ever display this contact information; they never execute an override themselves. Overriding a verdict is a human action outside Core/Maestro's scope.
- Admin-Override Evidence Principle: An admin override never mutates, replaces, or deletes an existing adjudication record — Core is stateless and re-evaluates from live PostgreSQL/Redis state rather than storing a mutable verdict, so there is no stored decision to flip. An override is validated (`src/supervisor/schemas.py`'s `OverrideRecord`: `extra="forbid"`, mandatory non-empty `justification`), authorized by pure, zero-I/O logic (`src/supervisor/logic.py`'s `evaluate_override`, mirroring `core/rules.py`'s pattern: fails closed if the issuer is unauthenticated, and fails closed if the claim being overridden has no existing adjudication record), and — only if accepted — emitted as its own distinct, SHA-256-signed `OverrideRecord` evidence entry (`evidence/emitter.py`'s `emit_override_evidence`), additive alongside the original `AdjudicationRecord`, linked only by `claim_id`. Core's determinism is preserved: the same claim always adjudicates the same way; an override is a separate, layered, human fact, never a rewritten decision. Exposed live at `POST /supervisor/override` (`src/supervisor/router.py`): 403 if the issuer is unauthenticated, 404 if the claim has no adjudication record on file (unlike `/airlock/claims`, where NO_GO is a valid outcome and always 200 — a rejected *override* means the administrative action didn't happen at all, so it uses real HTTP status semantics instead). Both the original adjudication and the override are now genuinely persisted, append-only, in PostgreSQL — `src/evidence/models.py`/`repository.py` (`adjudication_records`, written by `src/airlock/router.py` right after `emit_evidence()`) and `src/supervisor/models.py`/`repository.py` (`override_records`, written only when an override is accepted, never for rejections). Neither table uses `claim_id` as a primary key: these are audit logs, not latest-state tables, so multiple entries per claim are permitted by design.
- Admin-Override Notification Principle: An accepted override triggers a Maestro alert. Reasoning: an override is exactly the kind of event someone other than the overriding supervisor should hear about — a human just intervened in a fail-closed system, and staying silent about that would undercut the whole point of an audit trail. The alert is built from the existing `OutboundAlert` schema (decision `GO`, a single `rule_trace` entry describing the override, `escalation_contact` naming the overriding issuer) and sent through the existing `WhatsAppAdapter`/`TelegramAdapter` stubs — no new Maestro surface added. This is the *only* place `src/supervisor/` imports `src/maestro/`: `src/supervisor/logic.py`'s `evaluate_override` stays fully decoupled (zero Maestro imports), only `src/supervisor/router.py` (the orchestration layer) wires the two together, mirroring how `src/airlock/router.py` wires Core and Evidence without either depending on the router. This principle covers *override* notifications only — whether every ordinary `/airlock/claims` adjudication should also push a Maestro alert is a separate, still-open decision (below).
- Schema Provisioning Principle: Schema creation is automatic and idempotent, never a manual step. `src/core/init_db.py` runs once per container start (wired into the Dockerfile's `CMD`, before `uvicorn`) and calls `Base.metadata.create_all()` against `src/core/models.py`'s shared `Base.metadata` — the single source of truth for the schema, so there is no second copy of it (e.g. a raw init-SQL script) to drift out of sync. Every module defining a table (`src/core/models.py`, `src/evidence/models.py`, `src/supervisor/models.py`) must be imported inside `init_db.py`, even if none of its classes are referenced directly — SQLAlchemy only registers a table on `Base.metadata` once its model class has actually been imported somewhere, and this was caught as a real bug during live verification (only `authorized_issuers` was created until the other two model modules were explicitly imported). It retries briefly, since `docker-compose`'s `depends_on` only waits for the `db` container's process to start, not for Postgres itself to be ready. `create_all()` (not Alembic) is the deliberate choice for this project's stage: no production data, no schema-evolution history, one developer — Alembic's versioned-migration machinery would add real overhead (autogenerate discipline, upgrade/downgrade scripts in the deploy path) for no current benefit. Revisit this choice once a schema change needs to preserve existing data, or once `create_all()`'s create-only semantics (no `ALTER`s) become limiting. No seed data ships automatically — `scripts/seed_dev_data.py` is a separate, optional, manually-run script for local dev convenience only.

## Open Items
- Docker path — **RESOLVED 2026-07-28.** Docker Desktop is now installed. `docker compose up --build` builds and starts all three containers (`app`, `db`, `cache`) cleanly — verified via container logs (Postgres "database system is ready to accept connections", Redis "Ready to accept connections tcp", Uvicorn "Application startup complete") and via `pg_isready`/`redis-cli ping` in-container. All three ports (5432, 6379, 8000) confirmed reachable from the host. Full pytest suite: 54/54 passing with containers running (note: the HTTP-level tests still use dependency-injected fakes regardless of live infra — that's the locked testability principle, unchanged, not a gap). Beyond the test suite, a genuine live end-to-end request was made directly against the running containers (no mocks, no TestClient): `POST /airlock/claims` returned a real signed `200 GO` for a valid claim and a real signed `200 NO_GO` for an unknown zone — both replacing the earlier documented 500 fail-closed behavior from the non-Docker path.
- Maestro is not wired into `/airlock/claims` — **factual gap, narrower than before, still not a decision made.** As of 2026-07-28, an accepted admin-override *does* trigger a Maestro alert (see Admin-Override Notification Principle, above). But `src/airlock/router.py` itself still never calls into `src/maestro/` — an ordinary `POST /airlock/claims` adjudication (the vast majority of traffic) produces a signed evidence record but triggers no WhatsApp/Telegram delivery. Whether every adjudication should also push a Maestro alert (and if so, synchronously in the request path or some other mechanism) remains undecided.

## Technology Stack & Infrastructure
- Language & Runtime: Python 3.11+
- API Framework: FastAPI (Async, strict Pydantic v2 validation models)
- Database Layer: PostgreSQL (Rule registry & immutable audit storage) via SQLAlchemy Async
- Cache & State Store: Redis (Real-time zone state & telemetry cache)
- Containerization: Docker & Docker Compose
- Testing & Coverage: Pytest & HTTPX (100% path coverage required on adjudication logic)

## Repository Layout
```
synapse/
├── CLAUDE.md                   # System Constitution
├── docker-compose.yml          # Infrastructure orchestration (PostgreSQL, Redis, App)
├── Dockerfile
├── requirements.txt
├── src/
│   ├── main.py                 # FastAPI Entrypoint
│   ├── airlock/                # Schema validation & fail-closed ingestion
│   ├── core/                   # Deterministic Adjudication Engine (Pure functions)
│   │   ├── rules.py            # Pure rule checks (authority, zone safety)
│   │   ├── evaluator.py        # Pure adjudication orchestration
│   │   ├── models.py           # SQLAlchemy ORM models (rule registry) — schema source of truth
│   │   ├── repository.py       # PostgreSQL/Redis lookups (I/O boundary)
│   │   └── init_db.py          # Schema provisioning (create_all), run via Dockerfile CMD before uvicorn
│   ├── evidence/                # SHA-256 JSON-LD immutable log emitter
│   │   ├── emitter.py           # emit_evidence, emit_override_evidence (pure, no I/O)
│   │   ├── models.py            # AdjudicationAuditEntry (persisted, append-only)
│   │   └── repository.py        # persist/fetch adjudication records (I/O boundary)
│   ├── maestro/                 # Channel-agnostic delivery layer (wired for overrides only — see Open Items)
│   ├── supervisor/              # Admin-override: HTTP endpoint, pure logic, persistence
│   │   ├── schemas.py           # OverrideRecord (fail-closed input schema)
│   │   ├── logic.py             # evaluate_override (pure, no I/O)
│   │   ├── models.py            # OverrideAuditEntry (persisted, append-only)
│   │   ├── repository.py        # persist override records (I/O boundary)
│   │   └── router.py            # POST /supervisor/override — the only place this package touches Maestro
│   └── config.py               # Environment & state configurations
├── scripts/
│   └── seed_dev_data.py        # Optional, manual-only dev seed data — never run automatically
└── tests/
    ├── test_airlock.py         # Fail-closed schema boundary tests
    ├── test_adjudication.py    # Deterministic rule evaluation scenarios
    ├── test_evidence.py        # Cryptographic hashing & audit output tests (adjudication + override records)
    ├── test_maestro_*.py       # Maestro schema, formatting, and adapter tests
    ├── test_supervisor_schemas.py  # OverrideRecord schema validation
    ├── test_supervisor_logic.py    # evaluate_override pure-logic tests
    └── test_supervisor_router.py   # POST /supervisor/override: 422/403/404/200, evidence signing, Maestro notifications
```

## Developer Directives
- Implement, do not redesign. Architectural boundaries above are locked.
- Any payload missing required schema fields or failing validation MUST return HTTP 422 / Schema Error immediately — no silent defaults, no best-effort parsing.
- Keep `src/core/` pure, deterministic, and testable without database side-effects during evaluation.
