/**
 * <gateway-readiness> — Design Gateway readiness screen. UX PROTOTYPE ONLY.
 *
 * Built to test the interaction model for a future CORENET X / IFC+SG
 * readiness check, not to perform one. There is no backend route behind
 * this component, no repository/database read, and no rule evaluation —
 * unlike <blocked-screen> and <frontline-screen>, which render real,
 * persisted src/evidence/ records. Every byte of data this component
 * displays is caller-supplied fixture data (see demo.html) or, in the
 * repository, the zero-row shape described by src/ifc_sg/schemas.py's
 * SubmissionElementSpec — never a real IFC file, never a real SGPset_
 * field catalogue (none exists in this repo yet; see CLAUDE.md's IFC+SG
 * scope note and src/ifc_sg/schemas.py's module docstring).
 *
 * Because of that, this component breaks from <blocked-screen>/
 * <frontline-screen> in one deliberate way: it renders a permanent,
 * non-dismissible "EXAMPLE DATA" banner on every render, empty state
 * included. The existing components can skip that banner because their
 * data is always real (persisted evidence records); this one can't make
 * that claim, so the banner is load-bearing, not decoration — see
 * CLAUDE.md's fail-closed doctrine ("absence of data is not compliance")
 * applied to the display layer itself: a screen that *looks* like a real
 * compliance check but isn't would be exactly that failure.
 *
 * GO/NO_GO philosophy reused from <blocked-screen>/<frontline-screen>:
 * - The overall decision ("READY" / "NOT READY", this screen's GO/NO_GO
 *   vocabulary) is caller-supplied data, never computed in this
 *   component. Same discipline as CLAUDE.md's "Core decides, Maestro
 *   delivers" — a display component still isn't where a verdict gets
 *   decided, even a mocked one.
 * - Per-element MISSING/COMPLETE status *is* derived here, but only as a
 *   display transform of already-provided per-field `present` booleans
 *   — the same kind of derivation <blocked-screen>'s _renderConflict()
 *   does over rule_trace's `passed` booleans, not new decision logic.
 * - Progressive disclosure: element types collapse to a one-line summary
 *   (type, status, assigned actor) via native <details>/<summary> —
 *   keyboard-operable and screen-reader-friendly with no ARIA
 *   reimplementation needed — and expand to the field-level checklist,
 *   mirroring <blocked-screen>'s "Evaluated conditions" list.
 *
 * Input shape:
 *   data = {
 *     projectId: string,        // e.g. "PRJ-EXAMPLE-001" — fixture only
 *     jurisdictionCode: string, // e.g. "SG"
 *     decision: "GO" | "NO_GO", // caller-supplied, not computed here
 *     elements: [
 *       {
 *         elementType: string,    // e.g. "Door" — display label
 *         ifcClass: string,       // e.g. "IfcDoor" — placeholder, not a real mapping
 *         assignedRole: string,   // e.g. "BIM Coordinator" — invented placeholder title
 *         requiredFields: [
 *           { field: string, present: boolean },  // field names are PLACEHOLDER, not real SGPset_ data
 *         ],
 *       },
 *     ],
 *   }
 */
class GatewayReadiness extends HTMLElement {
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
      this.shadowRoot.innerHTML = `
        <style>${STYLE}</style>
        ${EXAMPLE_BANNER}
        <p class="empty">No readiness data loaded.</p>
      `;
      return;
    }

    const isBlocked = data.decision === "NO_GO";
    const elements = data.elements || [];
    const missingElements = elements.filter((el) => !this._isComplete(el));
    const actorsNeeded = [...new Set(missingElements.map((el) => el.assignedRole).filter(Boolean))];

    this.shadowRoot.innerHTML = `
      <style>${STYLE}</style>
      ${EXAMPLE_BANNER}
      <section class="screen ${isBlocked ? "blocked" : "cleared"}" role="status" aria-live="polite">
        <header>
          <span class="decision-badge">${escapeHtml(data.decision || "UNKNOWN")}</span>
          <span class="project-id">${escapeHtml(data.projectId || "—")}</span>
          ${data.jurisdictionCode ? `<span class="jurisdiction">${escapeHtml(data.jurisdictionCode)}</span>` : ""}
        </header>

        <p class="primary-instruction">
          ${isBlocked ? "Not ready to submit." : "Ready to submit."}
        </p>

        <h3>Elements checked (${elements.length})</h3>
        <ul class="element-list">
          ${elements.map((el) => this._renderElement(el)).join("")}
        </ul>

        <div class="who-needs-to-act">
          <strong>Who needs to act:</strong>
          ${
            actorsNeeded.length
              ? `<ul class="actor-list">${actorsNeeded.map((role) => `<li>${escapeHtml(role)}</li>`).join("")}</ul>`
              : `<span class="none">No outstanding action — every element is complete.</span>`
          }
        </div>
      </section>
    `;
  }

  _isComplete(element) {
    const fields = element.requiredFields || [];
    return fields.length > 0 && fields.every((f) => f.present === true);
  }

  _renderElement(element) {
    const fields = element.requiredFields || [];
    const complete = this._isComplete(element);
    const missingCount = fields.filter((f) => f.present !== true).length;

    return `
      <li class="element ${complete ? "complete" : "missing"}">
        <details>
          <summary>
            <span class="mark">${complete ? "✓" : "✗"}</span>
            <span class="element-type">${escapeHtml(element.elementType || "Unknown element")}</span>
            <span class="ifc-class">${escapeHtml(element.ifcClass || "")}</span>
            <span class="status-text">${complete ? "Complete" : `${missingCount} field(s) missing`}</span>
            ${element.assignedRole ? `<span class="assigned-role">${escapeHtml(element.assignedRole)}</span>` : ""}
          </summary>
          <ul class="field-list">
            ${fields.map((f) => this._renderField(f)).join("")}
          </ul>
        </details>
      </li>
    `;
  }

  _renderField(field) {
    const present = field.present === true;
    return `
      <li class="${present ? "present" : "absent"}">
        <span class="mark">${present ? "✓" : "✗"}</span>
        <span class="field-name">${escapeHtml(field.field)}</span>
        <span class="field-status">${present ? "present" : "missing"}</span>
      </li>
    `;
  }
}

const EXAMPLE_BANNER = `
  <div class="example-banner" role="note">
    ⚠ EXAMPLE DATA — placeholder element types and field names only. Not real SGPset_ data, not a compliance result.
  </div>
`;

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
  .example-banner {
    max-width: 480px; margin: 0 auto 0.75rem; padding: 0.6rem 0.85rem;
    background: #fff3cd; border: 2px solid #997404; color: #664d03;
    border-radius: 6px; font-size: 0.85rem; font-weight: 600; text-align: center;
  }
  .screen { max-width: 480px; margin: 0 auto; border-radius: 8px; padding: 1.25rem 1.25rem; border: 2px solid; }
  .screen.blocked { border-color: #b3261e; background: #fdecea; }
  .screen.cleared { border-color: #1e7d32; background: #eaf6ec; }
  header { display: flex; align-items: center; gap: 0.6rem; margin-bottom: 0.75rem; flex-wrap: wrap; }
  .decision-badge { font-weight: 700; letter-spacing: 0.05em; padding: 0.2rem 0.6rem; border-radius: 4px; background: rgba(0,0,0,0.08); }
  .project-id { font-family: monospace; }
  .jurisdiction { font-size: 0.8rem; padding: 0.1rem 0.4rem; border-radius: 3px; background: rgba(0,0,0,0.06); }
  .primary-instruction { font-weight: 800; font-size: 1.3rem; margin: 0 0 1rem; }
  .screen.blocked .primary-instruction { color: #b3261e; }
  .screen.cleared .primary-instruction { color: #1e7d32; }
  h3 { font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.04em; color: #444; margin: 0 0 0.5rem; }
  ul.element-list { list-style: none; padding: 0; margin: 0 0 1rem; display: flex; flex-direction: column; gap: 0.4rem; }
  .element { border-radius: 6px; border: 1px solid rgba(0,0,0,0.12); background: #fff; }
  .element.complete { border-left: 4px solid #1e7d32; }
  .element.missing { border-left: 4px solid #b3261e; }
  summary { display: flex; align-items: center; gap: 0.5rem; padding: 0.5rem 0.65rem; cursor: pointer; flex-wrap: wrap; }
  summary::-webkit-details-marker { color: #888; }
  .element.complete .mark { color: #1e7d32; }
  .element.missing .mark { color: #b3261e; }
  .element-type { font-weight: 600; }
  .ifc-class { font-family: monospace; font-size: 0.8rem; color: #666; }
  .status-text { font-size: 0.85rem; color: #444; }
  .assigned-role { margin-left: auto; font-size: 0.8rem; padding: 0.1rem 0.45rem; border-radius: 3px; background: rgba(0,0,0,0.06); }
  ul.field-list { list-style: none; margin: 0; padding: 0.4rem 0.65rem 0.65rem 2.1rem; border-top: 1px solid rgba(0,0,0,0.08); }
  ul.field-list li { display: flex; gap: 0.5rem; padding: 0.2rem 0; align-items: baseline; font-size: 0.85rem; flex-wrap: wrap; }
  ul.field-list li.present .mark { color: #1e7d32; }
  ul.field-list li.absent .mark { color: #b3261e; }
  .field-name { font-family: monospace; word-break: break-all; }
  .field-status { color: #666; }
  .who-needs-to-act { padding: 0.6rem 0.75rem; background: rgba(0,0,0,0.04); border-radius: 6px; font-size: 0.9rem; }
  .actor-list { margin: 0.35rem 0 0; padding-left: 1.2rem; }
  .who-needs-to-act .none { color: #1e7d32; }
  .empty { color: #555; font-style: italic; text-align: center; max-width: 480px; margin: 0 auto; }

  @media (max-width: 360px) {
    .screen { padding: 1rem 0.85rem; }
    .primary-instruction { font-size: 1.1rem; }
    .assigned-role { margin-left: 0; }
  }
`;

customElements.define("gateway-readiness", GatewayReadiness);
