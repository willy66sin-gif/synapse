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
 * - role/aria-live (2026-08-31, GO Freshness Phase 1b, Part B): the open
 *   question left by Phase 1 above is now resolved, asymmetrically, for the
 *   poll-triggered re-render path only. Ported from blocked-screen.js's own
 *   locked reasoning (quoted verbatim -- see that file's class doc comment):
 *   "role="alert" is reserved for a genuinely critical, dynamic change: an
 *   already-rendered verdict changing (decision or reason_code) to land on
 *   NO_GO. It is never applied on first render (nothing has "changed" yet
 *   at first paint) and never for a GO/cleared result." Applied here as:
 *   a poll-detected GO -> NO_GO transition renders with role="alert" /
 *   aria-live="assertive" (treated the same as blocked-screen.js's real-time
 *   hazard broadcast handling -- a worker may be mid-decision or about to
 *   act; an interrupting signal is justified); a poll-detected NO_GO -> GO
 *   transition renders role="status"/aria-live="polite" (no elevated-risk
 *   case for interrupting); an unchanged poll result leaves whatever
 *   role/live is already in effect untouched (see the `data` setter below --
 *   it only recomputes on a genuine transition), so an unchanged tick never
 *   flips an already-alerting region back down and never re-announces a
 *   state that hasn't changed. The initial server-rendered page load is
 *   unaffected: first render always has no previous data to compare
 *   against, so it keeps the constructor's status/polite default exactly as
 *   it did before this pass.
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
    this._pollTimer = null;
    this._regionRole = "status";
    this._regionLive = "polite";
  }

  set data(value) {
    const previousData = this._data;
    const nextData = value;

    const isRealVerdictChange =
      previousData != null &&
      nextData != null &&
      (previousData.decision !== nextData.decision || previousData.reasonCode !== nextData.reasonCode);

    // Only recompute role/live on a genuine transition -- see the
    // class doc comment's 2026-08-31 addendum for why an unchanged
    // poll result must leave whatever is already in effect untouched.
    if (isRealVerdictChange) {
      const isDynamicNoGoTransition = nextData.decision === "NO_GO";
      this._regionRole = isDynamicNoGoTransition ? "alert" : "status";
      this._regionLive = isDynamicNoGoTransition ? "assertive" : "polite";
    }

    this._data = value;
    this._render();
    this._ensurePolling();
  }

  get data() {
    return this._data;
  }

  connectedCallback() {
    this._render();
    this._ensurePolling();
  }

  disconnectedCallback() {
    this._stopPolling();
  }

  // GO Freshness Phase 1 (2026-08-31, Willy-authorized). Polling, not
  // push -- see src/frontline/router.py's frontline_status_json() doc
  // comment for why. Starts once claimId is known (from either the
  // server-rendered initial payload or a prior poll response) and is
  // idempotent -- safe to call from both the data setter and
  // connectedCallback without spawning a second interval.
  _ensurePolling() {
    if (this._pollTimer || !this._data || !this._data.claimId) return;
    this._pollTimer = setInterval(() => this._poll(), POLL_INTERVAL_MS);
  }

  _stopPolling() {
    if (this._pollTimer) {
      clearInterval(this._pollTimer);
      this._pollTimer = null;
    }
  }

  // Reassigns `this.data` on a successful response so the exact same
  // setter/_render() path handles a polled update as handles the
  // initial server-rendered payload -- no separate render path, no
  // per-field inspection of what changed. A network failure or non-OK
  // response leaves the last known-good render on screen and retries
  // on the next tick; it is not surfaced as an error state (out of
  // this pass's scope).
  async _poll() {
    if (!this._data || !this._data.claimId) return;
    try {
      const response = await fetch(`/frontline/blocked/${encodeURIComponent(this._data.claimId)}/status`);
      if (!response.ok) return;
      this.data = await response.json();
    } catch {
      // Network hiccup: keep showing the last known-good state.
    }
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
      <section class="screen ${isBlocked ? "blocked" : "cleared"}" role="${this._regionRole}" aria-live="${this._regionLive}">
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

// GO Freshness Phase 1b, Part C (2026-08-31): STILL PROVISIONAL, not a
// locked decision -- raised from Phase 1's 5000ms placeholder to
// 15000ms reflecting Willy's own reasoning (the staleness bound should
// be shorter than a typical work-decision window, not shorter than a
// human blink), but NOT yet checked against Ivan Lim's practitioner
// experience with actual site connectivity and live dashboard polling
// behavior. Kept as the one named constant to change again once that
// input comes in -- do not treat 15000 as settled.
const POLL_INTERVAL_MS = 15000;

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
