/**
 * <frontline-screen> — Frontline Worker verdict display.
 *
 * First implementation of the Frontline Worker persona (CLAUDE.md's Stage 2
 * Frontline Worker Contract, approved 1 Aug, previously unbuilt) and the
 * first consumer of the Escalation vs. Override — Decoupling Principle
 * (Locked, 5 Aug 2026). Deliberately separate from <blocked-screen>, which
 * is supervisor-facing: this component has no rule-trace list, no rule IDs,
 * no evidence-signature detail, no override form, and no code path that can
 * reach /supervisor/override.
 *
 * Content model (spec Section 7 — design, not repository doctrine):
 *   work activity  ->  GO/DO NOT PROCEED (primary)  ->  reason (NO_GO only)
 *   ->  "Contact: {assignedRole}"  ->  trace ID (secondary, non-interactive)
 *
 * Copy: "You may proceed." / "Do not proceed." are CLAUDE.md's own approved
 * Stage 1 Operational Stories wording, not invented here.
 *
 * assignedRole must be the actually-resolved authority-directory role
 * (src/maestro/directory.py's resolve_authority(), today always "General
 * Duty Officer" — the directory's only entry). Per the Escalation vs.
 * Override Decoupling Principle, this component must never hardcode
 * "Supervisor" or any other unverified title, and must never render an
 * override button, override language, or an override URL.
 *
 * Input shape:
 *   data = {
 *     claimId: string,
 *     decision: "GO" | "NO_GO",
 *     reasonCode: string | null,
 *     reason: string,        // plain-language only; "" on GO
 *     workActivity: string,  // e.g. "MATERIAL_ENTRY" (ClaimPayload.action_type)
 *     traceId: string,       // e.g. "BIND-999" — secondary/reference only
 *     assignedRole: string,  // e.g. "General Duty Officer"
 *   }
 *
 * Accessibility pass (2026-08-05):
 * - Contrast: every text/background pair verified >= 4.5:1 (WCAG 1.4.3).
 *   .trace-id's original #777 measured 3.92:1/4.03:1 against the two screen
 *   backgrounds -- a real failure, not a style preference -- darkened to
 *   #666 (5.02:1/5.17:1) while keeping it visually lightest/smallest/last,
 *   so it still reads as reference-only, never competing with the decision.
 * - role="status" / aria-live="polite" on every render, deliberately not
 *   "assertive" for NO_GO. This mirrors blocked-screen.js's own locked
 *   reasoning and CLAUDE.md's Stage 2 Frontline Worker Contract ("role=alert
 *   used only where appropriate for dynamic critical state changes -- not
 *   as a default"): assertive/alert semantics are for an already-rendered
 *   screen changing state, and this component has no such transition --
 *   data is set once, server-side, on page load. There is no "first
 *   render vs. later render" distinction here for alert semantics to
 *   apply to; every render is the only render.
 * - Reflow: no fixed-width elements; .screen uses max-width with rem
 *   padding, verified live at 320px viewport width with no horizontal
 *   scrollbar (see @media rule below for the tightest breakpoint).
 * - Viewport: user-scalable is left at the browser default (not disabled)
 *   in every page that hosts this component -- see demo.html and
 *   src/frontline/router.py's rendered page.
 */
class FrontlineScreen extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._data = null;
  }

  set data(value) {
    this._data = value;
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
    if (!data) {
      this.shadowRoot.innerHTML = `<style>${STYLE}</style><p class="empty">No status loaded.</p>`;
      return;
    }

    const isBlocked = data.decision === "NO_GO";

    this.shadowRoot.innerHTML = `
      <style>${STYLE}</style>
      <section class="screen ${isBlocked ? "blocked" : "cleared"}" role="status" aria-live="polite">
        ${data.workActivity ? `<div class="work-activity">${escapeHtml(data.workActivity)}</div>` : ""}

        <p class="primary-instruction">${isBlocked ? ICON_BLOCKED : ICON_CLEARED}${isBlocked ? "Do not proceed." : "You may proceed."}</p>

        ${isBlocked && data.reason ? `<p class="reason">${escapeHtml(data.reason)}</p>` : ""}

        <div class="next-step">
          <span class="label">Contact:</span>
          <span class="role">${escapeHtml(data.assignedRole || "Not available")}</span>
        </div>

        ${data.traceId ? `<div class="trace-id">Ref: ${escapeHtml(data.traceId)}</div>` : ""}
      </section>
    `;
  }
}

// Decorative, aria-hidden: the primary-instruction text already carries the
// full meaning for screen readers. These exist so colour is never the only
// signal for sighted users (CLAUDE.md's Stage 2 Frontline Worker Contract,
// "text, icon, and colour together -- never colour alone") -- a colour-blind
// user gets the same check/cross shape distinction a sighted user gets from
// green/red. currentColor so each inherits its state's existing text colour
// (.screen.blocked/.cleared .primary-instruction) with no separate icon color
// to keep in sync.
const ICON_CLEARED = `<svg class="icon" viewBox="0 0 24 24" width="28" height="28" aria-hidden="true" focusable="false"><path fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" d="M5 13l5 5L19 7"/></svg>`;
const ICON_BLOCKED = `<svg class="icon" viewBox="0 0 24 24" width="28" height="28" aria-hidden="true" focusable="false"><path fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" d="M6 6l12 12M18 6L6 18"/></svg>`;

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
  .screen { max-width: 480px; margin: 0 auto; border-radius: 8px; padding: 1.5rem 1.25rem; border: 2px solid; text-align: center; }
  .screen.blocked { border-color: #b3261e; background: #fdecea; }
  .screen.cleared { border-color: #1e7d32; background: #eaf6ec; }
  .work-activity { font-size: 0.9rem; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase; color: #444; margin-bottom: 1rem; }
  .primary-instruction { font-weight: 800; font-size: 1.75rem; line-height: 1.2; margin: 0 0 0.75rem; display: flex; align-items: center; justify-content: center; gap: 0.5rem; }
  .primary-instruction .icon { flex-shrink: 0; }
  .screen.blocked .primary-instruction { color: #b3261e; }
  .screen.cleared .primary-instruction { color: #1e7d32; }
  .reason { font-size: 1rem; margin: 0 0 1.25rem; color: #333; }
  .next-step { min-height: 48px; display: flex; align-items: center; justify-content: center; gap: 0.4rem; padding: 0.5rem 0.75rem; background: rgba(0,0,0,0.05); border-radius: 6px; font-size: 1rem; }
  .next-step .label { font-weight: 600; }
  .trace-id { margin-top: 0.75rem; font-size: 0.8rem; font-family: monospace; color: #666; }
  .empty { color: #555; font-style: italic; text-align: center; }

  @media (max-width: 360px) {
    .screen { padding: 1rem 0.85rem; }
    .primary-instruction { font-size: 1.5rem; }
  }
`;

customElements.define("frontline-screen", FrontlineScreen);
