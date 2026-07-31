# Frontend

No frontend stack existed in this repo before `blocked-screen/` — no `package.json`, no build tooling, no prior UI code of any kind. **The choice of a real frontend framework (React, Vue, Svelte, plain server-rendered templates, or something else) has not been made and is not made here** — see CLAUDE.md's Open Items. `blocked-screen/` is deliberately a zero-dependency vanilla-JS Web Component so it doesn't force that decision; it renders into any page via a single `<script type="module">` and works standalone (see `blocked-screen/demo.html`) or embedded inside whatever framework is eventually chosen.

## `blocked-screen/`

Supervisor-facing verdict display enforcing CLAUDE.md's Locked Design Principles by construction (see the component's own doc comment in `blocked-screen.js` for the exact mapping). Grounded in the real backend shapes, not assumed field names:

- **Verdict / evidence** (`src/core/evaluator.py`'s `Verdict`, as emitted by `src/evidence/emitter.py`'s `emit_evidence()` and returned by `POST /airlock/claims`): `claim_id`, `decision`, `reason`, `reason_code`, `rule_trace` (list of `{rule_id, passed, reason}`, mirroring `src/core/rules.py`'s `RuleOutcome`), `evaluated_at`.
- **Override submission** (`src/supervisor/schemas.py`'s `OverrideRecord`, posted to the real `POST /supervisor/override`, not a mock): `claim_id`, `issuer_id`, `justification`, `timestamp` — exactly these four fields, since the backend schema is `extra="forbid"` and rejects anything else at 422.
- **Escalation contact**: modeled the same way `src/maestro/schemas.py`'s `OutboundAlert.from_evidence_record()` already treats it — operational contact info that cannot be derived from the evidence record, supplied by the caller.

### `telemetry_signed` — placeholder, not implemented trust

`data.telemetrySigned` is a single optional boolean rendered as a badge (signed / unsigned / unknown). It does **not** correspond to any real backend field today — `src/core/rules.py`'s `ZoneRecord` has no such attribute, and there is no signing, attestation, HSM, or device-PKI mechanism anywhere in this codebase. It exists purely so the Blocked Screen has *somewhere* to show telemetry trust state once that work exists, without this component needing to change shape later. Treat any `true`/`false` value passed to it today as a caller-supplied placeholder, not a verified fact.

### Open item this component surfaces

- **No frontend stack decided** — flagged here rather than picked; see CLAUDE.md's Open Items.
- `reason_code` is available on `evidence` (threaded through as of the 2026-07-31 `emit_evidence()` change) and is rendered next to the conflicting condition, but it is **not** currently on `OutboundAlert` (Maestro's contract) — see CLAUDE.md's still-open "Downstream consumption of `reason_code` unconfirmed" item. This component reads it straight from the evidence record, not from Maestro.

### Try it

Open `demo.html` directly in a browser (no server, no build step) for static fixture data. To exercise the real override flow, serve it alongside a running API (`docker compose up` / `uvicorn src.main:app`) so `fetch("/supervisor/override")` in `demo.html`'s first fixture resolves.
