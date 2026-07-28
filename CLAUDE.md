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
- Admin-Override Evidence Principle: An admin override never mutates, replaces, or deletes an existing adjudication record — Core is stateless and re-evaluates from live PostgreSQL/Redis state rather than storing a mutable verdict, so there is no stored decision to flip. An override is validated (`src/supervisor/schemas.py`'s `OverrideRecord`: `extra="forbid"`, mandatory non-empty `justification`), authorized by pure, zero-I/O logic (`src/supervisor/logic.py`'s `evaluate_override`, mirroring `core/rules.py`'s pattern: fails closed if the issuer is unauthenticated, and fails closed if the claim being overridden has no existing adjudication record), and — only if accepted — emitted as its own distinct, SHA-256-signed `OverrideRecord` evidence entry (`evidence/emitter.py`'s `emit_override_evidence`), additive alongside the original `AdjudicationRecord`, linked only by `claim_id`. Core's determinism is preserved: the same claim always adjudicates the same way; an override is a separate, layered, human fact, never a rewritten decision.

## Open Items
- Admin-override HTTP surface — **still open.** The override *logic* is now locked (above): schema, authorization/existence checks, and evidence emission are implemented and tested (`tests/test_supervisor_schemas.py`, `tests/test_supervisor_logic.py`, and the override-evidence tests in `tests/test_evidence.py`). Not built, and deliberately out of scope for that pass:
  - A live authenticated endpoint (e.g. `POST /supervisor/overrides`) — `src/supervisor/` currently has no `router.py` and is not mounted in `src/main.py`.
  - A real persistence layer. There is currently no persisted evidence store anywhere in this codebase — `emit_evidence()`/`emit_override_evidence()` are both pure, non-persisting functions that only return a signed dict. `evaluate_override()`'s "does this claim have an adjudication record" check takes an already-resolved `Optional[dict]`, dependency-injected exactly like Core's `IssuerRecord`/`ZoneRecord`, so it's fully tested without this existing yet — but a real endpoint can't answer that question for real claims until something persists evidence records.
  - Whether a successful override triggers a Maestro alert — deliberately not wired. `src/supervisor/` has zero imports from `src/maestro/` (verified), keeping the same decoupling Core already has from delivery concerns.
- Docker path — **unverified.** Docker is still not installed on the development machine (confirmed again 2026-07-28: `docker`/`docker compose` not found, no install under `Program Files`). `docker-compose.yml`/`Dockerfile` have not been execution-tested. The non-Docker path (venv + `uvicorn`) is verified working for `/health`/`/docs`; `/airlock/claims` correctly fails loudly without live PostgreSQL/Redis (Fail-Closed Doctrine holds outside the test suite too). Not attempting install without explicit sign-off — see README.md.
- Maestro is not wired into the live app — **factual gap, not yet a decision to make.** `src/airlock/router.py` never calls into `src/maestro/`; confirmed via full import-graph audit (2026-07-28). `POST /airlock/claims` today produces a signed evidence record but triggers no WhatsApp/Telegram delivery. Maestro exists as tested, standalone code only. Wiring it in (and deciding whether that happens synchronously in the request path or via some other mechanism) is undecided.

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
│   │   ├── models.py           # SQLAlchemy ORM models (rule registry)
│   │   └── repository.py       # PostgreSQL/Redis lookups (I/O boundary)
│   ├── evidence/                # SHA-256 JSON-LD immutable log emitter (emit_evidence, emit_override_evidence)
│   ├── maestro/                 # Channel-agnostic delivery layer (not yet wired into main.py — see Open Items)
│   ├── supervisor/              # Admin-override request schema + pure authorization logic (no router yet — see Open Items)
│   └── config.py               # Environment & state configurations
└── tests/
    ├── test_airlock.py         # Fail-closed schema boundary tests
    ├── test_adjudication.py    # Deterministic rule evaluation scenarios
    ├── test_evidence.py        # Cryptographic hashing & audit output tests (adjudication + override records)
    ├── test_maestro_*.py       # Maestro schema, formatting, and adapter tests
    └── test_supervisor_*.py    # OverrideRecord schema and evaluate_override logic tests
```

## Developer Directives
- Implement, do not redesign. Architectural boundaries above are locked.
- Any payload missing required schema fields or failing validation MUST return HTTP 422 / Schema Error immediately — no silent defaults, no best-effort parsing.
- Keep `src/core/` pure, deterministic, and testable without database side-effects during evaluation.
