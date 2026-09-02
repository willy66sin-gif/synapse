/**
 * <blocked-screen> — supervisor-facing verdict display.
 *
 * Enforces CLAUDE.md's Locked Design Principles by construction:
 *
 * - Supervisor UI Principle: never renders a standalone decision. Every
 *   render shows the Rule ID and pass/fail of every evaluated
 *   sub-condition (data.evidence.rule_trace), and for NO_GO, the specific
 *   conflicting condition is called out explicitly.
 * - Escalation Requirement: escalationContact is always rendered,
 *   regardless of decision — this component never lets a caller omit it,
 *   independent of whatever the backend does or doesn't enforce.
 *
 * Independent NO_GO remediation (2026-08-01, scoped narrowly — see
 * CLAUDE.md's Stage 2 Frontline Worker Contract for the full target-state
 * contract this does NOT implement):
 * - NO_GO renders an unmistakable primary instruction, "Do not proceed."
 *   GO intentionally gets no equivalent "You may proceed" copy here —
 *   out of scope for this pass, which is NO_GO-only.
 * - role="alert" is reserved for a genuinely critical, dynamic change: an
 *   already-rendered verdict changing (decision or reason_code) to land
 *   on NO_GO. It is never applied on first render (nothing has "changed"
 *   yet at first paint) and never for a GO/cleared result — computed once
 *   per real `data` assignment in the setter below, not blanket-applied.
 *   Override-form-only re-renders (pending/success/error) reuse whatever
 *   role the last real verdict assignment set; they never flip it.
 *
 * Explicitly NOT touched in this pass: GO freshness/expiry/revalidation,
 * telemetry assurance beyond the existing signed/unsigned/unknown
 * placeholder, any new reason codes, BIND-999 interactivity (still none).
 *
 * No framework dependency: no repo-wide frontend stack exists yet (no
 * package.json, no frontend/ directory prior to this component) — see
 * frontend/README.md. This is deliberately a zero-dependency Custom
 * Element so it doesn't make that stack decision unilaterally; it can be
 * dropped into any framework's markup, or wrapped later, once that
 * decision is made.
 *
 * Input shape (see frontend/README.md for the full grounding):
 *   data = {
 *     evidence: {            // real src/evidence/emitter.py::emit_evidence() output
 *       claim_id: string,
 *       decision: "GO" | "NO_GO",
 *       reason: string,
 *       reason_code: string | null,
 *       authority_binding_id: string | null,  // real, persisted (src/maestro/directory.py's AuthorityBinding.binding_id) — null on GO
 *       rule_trace: Array<{ rule_id: string, passed: boolean, reason: string }>,
 *       evaluated_at: string,
 *     },
 *     escalationContact: string,   // supplied by caller, mirrors OutboundAlert.escalation_contact
 *     assignedRole: string | null | undefined,  // mirrors OutboundAlert.assigned_role — NOT persisted in
 *                                                // evidence (only authority_binding_id is); GET /supervisor/
 *                                                // blocked/{claim_id} resolves it live via resolve_authority()
 *                                                // as of 2026-08-06 (Task A) -- only a caller working from a
 *                                                // bare evidence record with no live resolution would leave
 *                                                // this unset now
 *     telemetrySigned: boolean | undefined,  // PLACEHOLDER — see README caveat
 *     overrideEndpoint: string,    // defaults to "/supervisor/override"
 *     issuerId: string,            // pre-fills the override form's issuer_id, if known
 *   }
 */
class BlockedScreen extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._data = null;
    this._overrideState = { status: "idle", message: "" }; // idle | pending | success | error
    this._regionRole = "status";
    this._regionLive = "polite";
  }

  set data(value) {
    const previousEvidence = this._data && this._data.evidence;
    const nextEvidence = value && value.evidence;

    // See the class doc comment: alert semantics only for a genuinely
    // critical, dynamic transition into NO_GO on an already-rendered
    // screen -- not on first render, not for GO.
    const isRealVerdictChange =
      previousEvidence != null &&
      nextEvidence != null &&
      (previousEvidence.decision !== nextEvidence.decision ||
        previousEvidence.reason_code !== nextEvidence.reason_code);
    const isDynamicNoGoTransition = isRealVerdictChange && nextEvidence.decision === "NO_GO";

    this._regionRole = isDynamicNoGoTransition ? "alert" : "status";
    this._regionLive = isDynamicNoGoTransition ? "assertive" : "polite";

    this._data = value;
    this._overrideState = { status: "idle", message: "" };
    this._render();
  }

  get data() {
    return this._data;
  }

  connectedCallback() {
    this._render();
  }

  _render() {
    const data = this._data;
    if (!data || !data.evidence) {
      this.shadowRoot.innerHTML = `<style>${STYLE}</style><p class="empty">No verdict loaded.</p>`;
      return;
    }

    const evidence = data.evidence;
    const isBlocked = evidence.decision === "NO_GO";
    const conflicting = isBlocked
      ? (evidence.rule_trace || []).find((rule) => rule.passed === false) || null
      : null;

    this.shadowRoot.innerHTML = `
      <style>${STYLE}</style>
      <section class="screen ${isBlocked ? "blocked" : "cleared"}" role="${this._regionRole}" aria-live="${this._regionLive}">
        <header>
          <span class="decision-badge">${escapeHtml(evidence.decision)}</span>
          <span class="claim-id">Claim ${escapeHtml(evidence.claim_id)}</span>
        </header>

        ${isBlocked ? `<p class="primary-instruction">Do not proceed.</p>` : ""}

        ${isBlocked ? this._renderConflict(conflicting, evidence.reason_code, evidence.reason) : ""}

        <h3>Evaluated conditions</h3>
        <ul class="rule-trace">
          ${(evidence.rule_trace || []).map((rule) => this._renderRule(rule)).join("")}
        </ul>

        ${this._renderTelemetryBadge(data.telemetrySigned)}

        <div class="escalation">
          <strong>Escalate / request override:</strong>
          <span>${escapeHtml(data.escalationContact || "No escalation contact provided.")}</span>
          ${this._renderAuthorityBinding(evidence.authority_binding_id, data.assignedRole)}
        </div>

        ${this._renderOverrideForm(evidence.claim_id, data.issuerId)}
      </section>
    `;

    const form = this.shadowRoot.querySelector("form.override-form");
    if (form) {
      form.addEventListener("submit", (event) => this._handleOverrideSubmit(event, data));
    }
  }

  _renderConflict(conflicting, reasonCode, reason) {
    // Reason de-duplication (2026-09-02, Frontline/Supervisor
    // consistency follow-up, Item 1): `reason` is evidence.reason,
    // read straight through -- the single value
    // src/core/evaluator.py's adjudicate() computed once, at
    // adjudication time. This function no longer re-derives it from
    // `conflicting.reason` (the matched rule_trace entry); `conflicting`
    // is kept only to label WHICH rule failed (rule_id), a genuinely
    // separate concern from the reason text itself. The two happened
    // to always agree (adjudicate() short-circuits on the first
    // failing rule, so its Verdict.reason and that rule's own reason
    // are the same string by construction) -- but that was an
    // unasserted invariant two independent implementations (this one,
    // and the removed src/frontline/router.py::_frontline_reason())
    // both silently depended on, not a shared source of truth.
    if (!conflicting) {
      return `
        <div class="conflict missing">
          <strong>Conflicting condition:</strong>
          <span class="rule-id missing">rule_id unavailable — no failing condition was found in rule_trace</span>
          ${reasonCode ? `<span class="reason-code">${escapeHtml(reasonCode)}</span>` : ""}
          <p class="reason">${escapeHtml(reason)}</p>
        </div>
      `;
    }
    return `
      <div class="conflict">
        <strong>Conflicting condition:</strong>
        <span class="rule-id">${escapeHtml(conflicting.rule_id)}</span>
        ${reasonCode ? `<span class="reason-code">${escapeHtml(reasonCode)}</span>` : ""}
        <p class="reason">${escapeHtml(reason)}</p>
      </div>
    `;
  }

  _renderAuthorityBinding(authorityBindingId, assignedRole) {
    if (!authorityBindingId && !assignedRole) return "";
    const parts = [];
    if (assignedRole) parts.push(escapeHtml(assignedRole));
    if (authorityBindingId) parts.push(`(${escapeHtml(authorityBindingId)})`);
    return `<div>Escalation owner: ${parts.join(" ")}</div>`;
  }

  _renderRule(rule) {
    const passed = rule.passed === true;
    return `
      <li class="${passed ? "pass" : "fail"}">
        <span class="mark">${passed ? "✓" : "✗"}</span>
        <span class="rule-id">${escapeHtml(rule.rule_id)}</span>
        <span class="reason">${escapeHtml(rule.reason)}</span>
      </li>
    `;
  }

  _renderTelemetryBadge(telemetrySigned) {
    // PLACEHOLDER: telemetry_signed is a lightweight boolean flag only.
    // No signing/attestation, no HSM, no device PKI — see frontend/README.md.
    let label;
    let cls;
    if (telemetrySigned === true) {
      label = "Telemetry signed";
      cls = "signed";
    } else if (telemetrySigned === false) {
      label = "Telemetry unsigned";
      cls = "unsigned";
    } else {
      label = "Telemetry signing status unknown";
      cls = "unknown";
    }
    return `<div class="telemetry-badge ${cls}" title="Placeholder trust flag — not a cryptographic verification.">${label}</div>`;
  }

  _renderOverrideForm(claimId, issuerId) {
    const state = this._overrideState;
    return `
      <form class="override-form">
        <h3>Request override</h3>
        <label>
          Issuer ID
          <input name="issuer_id" required value="${escapeHtml(issuerId || "")}" />
        </label>
        <label>
          Justification
          <textarea name="justification" required minlength="1"></textarea>
        </label>
        <input type="hidden" name="claim_id" value="${escapeHtml(claimId)}" />
        <button type="submit" ${state.status === "pending" ? "disabled" : ""}>
          ${state.status === "pending" ? "Submitting..." : "Submit override"}
        </button>
        ${state.status === "success" ? `<p class="override-result success">${escapeHtml(state.message)}</p>` : ""}
        ${state.status === "error" ? `<p class="override-result error">${escapeHtml(state.message)}</p>` : ""}
      </form>
    `;
  }

  async _handleOverrideSubmit(event, data) {
    event.preventDefault();
    const form = event.target;
    const formData = new FormData(form);

    // Matches src/supervisor/schemas.py's OverrideRecord exactly —
    // extra="forbid" on the backend means sending any other key 422s.
    const body = {
      claim_id: formData.get("claim_id"),
      issuer_id: formData.get("issuer_id"),
      justification: formData.get("justification"),
      timestamp: new Date().toISOString(),
    };

    this._overrideState = { status: "pending", message: "" };
    this._render();

    const endpoint = data.overrideEndpoint || "/supervisor/override";

    try {
      const response = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });

      const payload = await response.json().catch(() => ({}));

      if (response.ok) {
        this._overrideState = {
          status: "success",
          message: `Override accepted. ${(payload.notifications || []).length} notification(s) sent.`,
        };
      } else {
        this._overrideState = {
          status: "error",
          message: formatErrorDetail(payload.detail, response.status),
        };
      }
    } catch (err) {
      this._overrideState = {
        status: "error",
        message: `Request failed: ${err.message}`,
      };
    }

    this._render();
    this.dispatchEvent(
      new CustomEvent("override-result", { detail: this._overrideState, bubbles: true, composed: true })
    );
  }
}

function formatErrorDetail(detail, status) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    // FastAPI/Pydantic 422 validation error shape: [{loc, msg, type}, ...]
    return detail.map((item) => item.msg || JSON.stringify(item)).join("; ");
  }
  return `Request failed with status ${status}.`;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[ch]);
}

const STYLE = `
  :host { display: block; font-family: system-ui, sans-serif; }
  .screen { border-radius: 8px; padding: 1rem 1.25rem; border: 2px solid; }
  .screen.blocked { border-color: #b3261e; background: #fdecea; }
  .screen.cleared { border-color: #1e7d32; background: #eaf6ec; }
  header { display: flex; align-items: center; gap: 0.75rem; margin-bottom: 0.75rem; }
  .decision-badge { font-weight: 700; letter-spacing: 0.05em; padding: 0.2rem 0.6rem; border-radius: 4px; background: rgba(0,0,0,0.08); }
  .claim-id { font-family: monospace; }
  .primary-instruction { font-weight: 700; font-size: 1.1rem; color: #b3261e; margin: 0 0 0.75rem; }
  .conflict { border: 1px solid #b3261e; background: #fff; border-radius: 6px; padding: 0.5rem 0.75rem; margin-bottom: 0.75rem; }
  .conflict.missing { color: #b3261e; font-weight: 600; }
  .reason-code { font-family: monospace; margin-left: 0.5rem; background: #eee; padding: 0.1rem 0.4rem; border-radius: 3px; }
  ul.rule-trace { list-style: none; padding: 0; margin: 0 0 0.75rem; }
  ul.rule-trace li { display: flex; gap: 0.5rem; padding: 0.25rem 0; align-items: baseline; }
  ul.rule-trace li.pass .mark { color: #1e7d32; }
  ul.rule-trace li.fail .mark { color: #b3261e; }
  .rule-id { font-family: monospace; font-weight: 600; }
  .telemetry-badge { display: inline-block; font-size: 0.85rem; padding: 0.2rem 0.5rem; border-radius: 4px; margin-bottom: 0.75rem; }
  .telemetry-badge.signed { background: #e0f2e9; color: #1e7d32; }
  .telemetry-badge.unsigned { background: #fdecea; color: #b3261e; }
  .telemetry-badge.unknown { background: #eee; color: #555; }
  .escalation { margin-bottom: 1rem; padding: 0.5rem 0.75rem; background: rgba(0,0,0,0.04); border-radius: 6px; }
  form.override-form { border-top: 1px solid rgba(0,0,0,0.1); padding-top: 0.75rem; display: flex; flex-direction: column; gap: 0.5rem; }
  form.override-form label { display: flex; flex-direction: column; gap: 0.25rem; font-size: 0.9rem; }
  form.override-form input, form.override-form textarea { padding: 0.4rem; border-radius: 4px; border: 1px solid #ccc; font: inherit; }
  form.override-form button { align-self: flex-start; padding: 0.5rem 1rem; border-radius: 4px; border: none; background: #1a56db; color: #fff; cursor: pointer; }
  form.override-form button:disabled { opacity: 0.6; cursor: not-allowed; }
  .override-result.success { color: #1e7d32; }
  .override-result.error { color: #b3261e; }
  .empty { color: #555; font-style: italic; }
`;

customElements.define("blocked-screen", BlockedScreen);
