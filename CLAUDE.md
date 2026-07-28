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
│   ├── evidence/                # SHA-256 JSON-LD immutable log emitter
│   └── config.py               # Environment & state configurations
└── tests/
    ├── test_airlock.py         # Fail-closed schema boundary tests
    ├── test_adjudication.py    # Deterministic rule evaluation scenarios
    └── test_evidence.py        # Cryptographic hashing & audit output tests
```

## Developer Directives
- Implement, do not redesign. Architectural boundaries above are locked.
- Any payload missing required schema fields or failing validation MUST return HTTP 422 / Schema Error immediately — no silent defaults, no best-effort parsing.
- Keep `src/core/` pure, deterministic, and testable without database side-effects during evaluation.
