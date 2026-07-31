# Frontend

No frontend stack existed in this repo before `blocked-screen/` — no `package.json`, no build tooling, no prior UI code of any kind. **The choice of a real frontend framework (React, Vue, Svelte, plain server-rendered templates, or something else) has not been made and is not made here** — see CLAUDE.md's Open Items. `blocked-screen/` is deliberately a zero-dependency vanilla-JS Web Component so it doesn't force that decision; it renders into any page via a single `<script type="module">` and works standalone (see `blocked-screen/demo.html`) or embedded inside whatever framework is eventually chosen.

## `blocked-screen/`

Supervisor-facing verdict display enforcing CLAUDE.md's Locked Design Principles by construction (see the component's own doc comment in `blocked-screen.js` for the exact mapping). Grounded in the real backend shapes, not assumed field names:

- **Verdict / evidence** (`src/core/evaluator.py`'s `Verdict`, as emitted by `src/evidence/emitter.py`'s `emit_evidence()` and returned by `POST /airlock/claims`): `claim_id`, `decision`, `reason`, `reason_code`, `authority_binding_id` (added 2026-07-31, `None` on GO — see CLAUDE.md's Escalation Ownership Principle), `rule_trace` (list of `{rule_id, passed, reason}`, mirroring `src/core/rules.py`'s `RuleOutcome`), `evaluated_at`.
- **Override submission** (`src/supervisor/schemas.py`'s `OverrideRecord`, posted to the real `POST /supervisor/override`, not a mock): `claim_id`, `issuer_id`, `justification`, `timestamp` — exactly these four fields, since the backend schema is `extra="forbid"` and rejects anything else at 422.
- **Escalation contact / authority (as of 2026-07-31)**: `src/maestro/schemas.py`'s `OutboundAlert.from_evidence_record()` now resolves `recipient_id`/`escalation_contact`/`authority_binding_id`/`assigned_role` itself via `src/maestro/directory.py`'s `resolve_authority(zone_id, reason_code)` — no longer caller-supplied the way this doc originally described. `GET /supervisor/blocked/{claim_id}` (below) deliberately does *not* call `resolve_authority()` itself (out of scope for that route), so its `escalationContact` stays a plain string and `data.assignedRole` is left unset — only `evidence.authority_binding_id` (real, persisted) is populated live.

### `telemetry_signed` — placeholder, not implemented trust

`data.telemetrySigned` is a single optional boolean rendered as a badge (signed / unsigned / unknown). It does **not** correspond to any real backend field today — `src/core/rules.py`'s `ZoneRecord` has no such attribute, and there is no signing, attestation, HSM, or device-PKI mechanism anywhere in this codebase. It exists purely so the Blocked Screen has *somewhere* to show telemetry trust state once that work exists, without this component needing to change shape later. Treat any `true`/`false` value passed to it today as a caller-supplied placeholder, not a verified fact.

### Open item this component surfaces

- **No frontend stack decided** — flagged here rather than picked; see CLAUDE.md's Open Items.
- `reason_code` is available on both `evidence` and `OutboundAlert` now (see CLAUDE.md's Reason Code Convention). This component reads it straight from the evidence record, not from Maestro.

### Live: `GET /supervisor/blocked/{claim_id}`

As of 2026-07-31, the component is reachable in the running app, not just as a standalone demo — `src/supervisor/router.py`'s `GET /supervisor/blocked/{claim_id}` fetches the real persisted evidence record (`src/evidence/repository.py`'s `fetch_latest_adjudication_record()`, same helper `POST /supervisor/override` already uses) and serves the page with `<blocked-screen>`'s `data` populated from it via a JSON hand-off. 404 if the claim was never adjudicated; 409 if it exists but the decision is GO (this route only ever serves NO_GO — the Locked Supervisor UI Principle's "conflicting condition" requirement doesn't apply to GO, and this route doesn't try to). `blocked-screen.js` itself is served from `/static/blocked-screen/blocked-screen.js` (`src/main.py` mounts `frontend/` as `/static` — plain static file serving, not a build step). This live route does not call `resolve_authority()` (see above) — `demo.html`'s fixtures remain the only place `assignedRole` is shown populated today.

### Try it

Open `demo.html` directly in a browser (no server, no build step) for static fixture data. To exercise the real override flow, serve it alongside a running API (`docker compose up` / `uvicorn src.main:app`) so `fetch("/supervisor/override")` in `demo.html`'s first fixture resolves. For real persisted data, adjudicate a NO_GO claim via `POST /airlock/claims` and then visit `GET /supervisor/blocked/{claim_id}` on the running API.
