# Synapse Failure-Mode Catalogue

**Status:** New document, authored 2026-09-15, Willy-authorized as an exception to
the standing no-documentation-changes-without-authorization rule, in response to
an external reviewer's challenge (LinkedIn, AI/capital-allocation background):
claim-type-based routing is an average, consequence is only knowable after review,
and the real precondition for anyone to accept reliance on Synapse is a documented
failure-mode catalogue — classes of wrong output, trigger conditions, and what
confidently-incorrect output looks like from outside.

**What this document is not:** it is not a new architecture, not a new reason code,
not a new rule. Every entry below either cites a file/line already in the
repository, or is explicitly marked `[Proposed]` and kept out of the grounded
inventory. Nothing here changes `CLAUDE.md`, `src/core/rules.py`, or any other file.

**Grounding order and evidence labels** follow the conventions already in use for
Synapse research work (repository ground truth first; label every claim
`[Repository-grounded]` with a file/line citation, `[Continuity-grounded]`,
`[Proposed]`, or `[Unresolved]`). Repository state cited below: `C:\Users\USER\dev\synapse`,
branch `master`, HEAD `17b3334fb35f47d04b7417612e7c5ff5021c1770`, working tree clean
before this document was added.

---

## Part 1 — Reason-code inventory (implemented NO_GO output classes)

Every entry below is a currently-implemented, currently-reachable rejection with its
own `reason_code`. All of them are produced *after* a claim has already passed
Pydantic schema validation — i.e. Airlock accepted the shape of the request, and
either Core (`adjudicate()`) or an Airlock-stage business check rejected its
*content*. This is the dimension that distinguishes every code below from the
structural-rejection case in Part 1B.

### R-PTW-01 — ePTW Precondition Failure

- **Trigger:** `claim.work_type` is one of `EXCAVATION`/`LIFTING`/`HOT_WORK`/`CONFINED_SPACE`
  (`HIGH_RISK_WORK_TYPES`) and the submitted `ptw_context` is missing, not
  `APPROVED`, outside its `valid_from`/`valid_until` window, zone-mismatched,
  type-mismatched, or the issuer lacks an admissible role (`RTO`) for it.
  `[Repository-grounded]` `src/core/rules.py:309-409` (`verify_ptw_precondition`),
  `src/core/rules.py:128,160`.
- **Output shape:** `POST /airlock/claims` still returns **HTTP 200** (this
  endpoint's own convention — NO_GO is a valid outcome, not an HTTP error). Body is
  the signed `AdjudicationRecord`: `decision: "NO_GO"`, `reason_code: "R-PTW-01"`,
  a human-readable `reason` string (one of five variants depending on which
  sub-condition failed — see `src/core/rules.py:339-403`), `rule_trace` containing
  exactly one entry (`ptw_precondition_check`, `passed: false`) since Rule 0
  short-circuits before Rule 1/Rule 2 ever run, and `authority_binding_id`
  resolved to `["BIND-RTO-01"]`. `[Repository-grounded]`
  `src/core/evaluator.py:94-103`, `src/evidence/emitter.py:83-106`,
  `src/maestro/directory.py:326`. Also fires a Maestro alert (WhatsApp + Telegram
  stub adapters) synchronously in the request path. `[Repository-grounded]`
  `src/airlock/router.py:175-178`.
- **Distinguishing from a structural rejection:** a structural rejection (Part 1B)
  never reaches `adjudicate()` at all and carries no `reason_code`. This code means
  the claim's *shape* was fine — a real, well-formed high-risk-work claim was
  submitted — but its *permit content* failed a specific, named condition.

### R-AUTH-01 — Authority Failure (unauthenticated issuer)

- **Trigger:** `issuer_record` is `None` — the submitted `issuer_id` does not match
  any row Synapse recognizes. Deliberately not split further: "we don't recognize
  this issuer" carries no domain signal to split by claim content.
  `[Repository-grounded]` `src/core/rules.py:166-232` (`classify_authority_failure`),
  specifically lines 195-202, 223-224.
- **Output shape:** HTTP 200, `decision: "NO_GO"`, `reason_code: "R-AUTH-01"`,
  `reason: "Authority Failure: Issuer '<id>' is unauthenticated."`
  (`src/core/rules.py:241-246`), `rule_trace` has two entries (Rule 0 passed, Rule 1
  failed — Rule 2 never runs). Routes to `["BIND-RTO-01"]`.
  `[Repository-grounded]` `src/maestro/directory.py:327`.
- **Distinguishing from a structural rejection:** the claim's `issuer_id` field was
  present and correctly typed (a string) — Airlock has no way to validate whether
  an issuer *exists* at the schema level, only that the field is well-formed. This
  code is where "exists" gets checked, one layer downstream of schema validation.

### R-AUTH-02 — Authority Failure, high-risk work (insufficient admissible role)

- **Trigger:** issuer is recognized, but holds none of the roles in
  `GATE_ADMISSIBLE_ROLES["R-AUTH-02"]` (currently `{RTO}`), and `claim.work_type`
  is one of the four high-risk types. `[Repository-grounded]`
  `src/core/rules.py:158-163, 226-231`.
- **Output shape:** HTTP 200, `reason_code: "R-AUTH-02"`,
  `reason: "Authority Failure: Issuer '<id>' does not hold an admissible role
  (['RTO']) for this claim."` (`src/core/rules.py:248-256`). Routes to
  `["BIND-RTO-01"]`.
- **Important caveat, repository-confirmed, not a proposal:** as of the
  2026-08-28 R-ZONE-01/R-PTW-01 Admissibility pass, **this code is currently
  structurally unreachable in practice.** Rule 0 (`verify_ptw_precondition`)
  already gates the identical `RTO` role on the identical high-risk work types
  *before* Rule 1 ever runs — so a claim missing RTO for high-risk work is
  rejected at Rule 0 with `R-PTW-01` first, and Rule 1 (where `R-AUTH-02` would
  fire) is never reached for that claim. This is recorded in the repository as an
  accepted, intentional consequence, not a bug silently left in place.
  `[Repository-grounded]` `CLAUDE.md:304` (2026-08-28 changelog entry),
  `tests/test_adjudication.py::test_r_auth_02_is_unreachable_for_high_risk_work_given_a_valid_permit`.
  **This means an external caller should not expect to ever observe `R-AUTH-02` in
  practice today** — a code that exists, is documented, is tested for its own
  unreachability, but does not currently fire on any live path.
- **Distinguishing from a structural rejection:** same as R-AUTH-01 — a
  content-level check, not a shape check. (In practice this branch's real-world
  trigger is currently absorbed by R-PTW-01, per the caveat above.)

### R-AUTH-03 — Authority Failure, nominal work (insufficient admissible role)

- **Trigger:** issuer is recognized, lacks the admissible role (`RTO`), and
  `claim.work_type` is `NOMINAL_CIVIL` (not high-risk) — so Rule 0 doesn't gate it
  first, unlike R-AUTH-02's case. `[Repository-grounded]` `src/core/rules.py:226-231`.
- **Output shape:** HTTP 200, `reason_code: "R-AUTH-03"`, same `reason` text
  pattern as R-AUTH-02. Routes to `["BIND-RTO-01"]`.
- **Distinguishing from a structural rejection:** same as above.

### R-ZONE-01 — Safety Violation / Zone Safety Failure

- **Trigger:** three independent conditions collapse to this one code —
  (a) `zone_id` does not exist in Redis at all, (b) a `LIFT_OPERATION`
  `action_type` in a `HIGH` hazard-level zone, or (c) issuer lacks the admissible
  role (`SA`) for the zone. `[Repository-grounded]` `src/core/rules.py:261-306`.
- **Output shape:** HTTP 200, `reason_code: "R-ZONE-01"`, `reason` text varies by
  which of the three sub-conditions fired (`src/core/rules.py:284-304`),
  `rule_trace` has three entries (Rule 0 and Rule 1 both passed). Routes to
  `["BIND-SA-01"]` — the one authority-routing decision with the longest
  unambiguous confirmation history in the repo.
  `[Repository-grounded]` `src/maestro/directory.py:147-170,324`.
- **Distinguishing from a structural rejection:** all three sub-triggers require a
  well-formed claim referencing a specific zone and action — Airlock has no
  concept of "zone" validity at the schema level, only that `zone_id`/`action_type`
  are present and are strings.

### R-DEV-01 — Device Not Registered (telemetry ingestion)

- **Trigger:** `POST /telemetry/zone-state` names a `device_id` with no matching
  `DeviceRegistryEntry`. This is a *provisioning gap*, not a trust gap — the
  device has simply never been registered. `[Repository-grounded]`
  `src/telemetry/trust.py:49-65`.
- **Output shape:** **HTTP 422** (not 200 — this is a different endpoint from
  `/airlock/claims`, and its own convention treats a trust rejection as a request
  refusal at the boundary). Body: `{"reason_code": "R-DEV-01", "message": "<str>"}`.
  `[Repository-grounded]` `src/telemetry/router.py:86-89`. Also produces its own
  distinct signed evidence record, `SensorZoneStateRejectionRecord`
  (`source: "TELEMETRY_REJECTED"`, `attempted_value` recorded but not trusted),
  separate from the `AdjudicationRecord` trail entirely — this rejection never
  touches Core or a claim at all. `[Repository-grounded]`
  `src/evidence/emitter.py:140-174`.
- **Distinguishing from R-DEV-02 and from adjudication NO_GOs:** unlike
  R-PTW-01/R-AUTH-*/R-ZONE-01, this is not a Core adjudication outcome at all — it
  is a device-trust check on a *separate* ingestion pathway (sensor telemetry
  writing a `ZoneRecord` field), entirely decoupled from claim adjudication. There
  is no `claim_id` involved.

### R-DEV-02 — Telemetry Signature Invalid

- **Trigger:** `device_id` is registered, but the Ed25519 signature over the
  payload does not verify against its registered public key — a *trust gap*, not a
  provisioning gap (known device, bad/tampered signature).
  `[Repository-grounded]` `src/telemetry/trust.py:49-65`.
- **Output shape:** same shape as R-DEV-01 — HTTP 422,
  `{"reason_code": "R-DEV-02", "message": "<str>"}`, its own
  `SensorZoneStateRejectionRecord`. `[Repository-grounded]`
  `src/telemetry/router.py:90-93`.
- **Distinguishing from R-DEV-01:** both are 422s with the same body shape; only
  the `reason_code` value tells a caller which of the two actually happened
  (provisioning vs. trust). `tests/test_telemetry_zone_write.py::test_the_two_rejection_reason_codes_are_distinguishable_without_the_exception_type`
  exists specifically to guard this. `[Repository-grounded]`

### R-PROFILE-01 — Profile ID Missing

- **Trigger:** `settings.profile_id_enforcement_enabled` is `True` (default
  `False` — see Part 2, item 4) **and** the claim submitted no `profile_id` at
  all. `[Repository-grounded]` `src/airlock/profile_check.py:96-98`,
  `src/config.py:28`.
- **Output shape:** HTTP 422 (Airlock-stage rejection, before `adjudicate()` runs
  at all — this fires upstream of Core). Body:
  `{"reason_code": "R-PROFILE-01", "message": "No profile_id was submitted with
  this claim."}`. `[Repository-grounded]` `src/airlock/router.py:104-109`. Its
  own distinct evidence record, `ProfileRejectionRecord`, in its own audit table —
  not folded into `AdjudicationAuditEntry`, specifically because this claim never
  reached Core to be adjudicated. `[Repository-grounded]`
  `src/evidence/emitter.py:177-214`.
- **Distinguishing from a structural rejection:** this is the closest thing in the
  repo to a genuine intermediate category — it is *not* a Core adjudication NO_GO
  (no `Verdict`/`AdjudicationRecord` involved at all), but it is also not a plain
  Pydantic shape failure (`profile_id` is a valid, optional field; the claim's
  shape was fine). It is a **third category**: an Airlock-stage, business-rule,
  fail-closed rejection with its own reason code and evidence record, sitting
  structurally between "malformed shape" and "Core said no."

### R-PROFILE-02 — Profile ID Unresolvable

- **Trigger:** enforcement is `True` and a `profile_id` was submitted, but no
  `CertifiedProfileRecord` matches it. `[Repository-grounded]`
  `src/airlock/profile_check.py:105-109`.
- **Output shape:** same as R-PROFILE-01 — HTTP 422,
  `{"reason_code": "R-PROFILE-02", "message": "profile_id '<id>' does not match
  any registered CertifiedProfile."}`, its own `ProfileRejectionRecord`.
- **Distinguishing from R-PROFILE-01:** provisioning-gap vs. resolution-gap split,
  same pattern as R-DEV-01/R-DEV-02 — explicitly modeled on it.
  `[Repository-grounded]` `src/airlock/profile_check.py:14-29`.

---

## Part 1B — Structural rejection (no reason_code at all)

Not a `reason_code` class — included for contrast, since it is the baseline every
code above is implicitly compared against. Any `POST /airlock/claims` body that
fails Pydantic validation against `ClaimPayload` (missing required field, wrong
type, unknown/extra field, unstructured prose, malformed JSON) is rejected with
**HTTP 422** by FastAPI/Pydantic before any application code in
`src/airlock/router.py` runs at all. `[Repository-grounded]`
`src/airlock/schemas.py:1-7,69,88`; `tests/test_airlock.py:123-149`. There is no
`reason_code` field in this response at all (it's FastAPI's own generic
validation-error body, not one of Synapse's signed evidence records), and no
evidence record is emitted — the claim never became a "claim" as this system
defines one.

**This is the practical test for "clean rejection vs. everything else":** if the
response has no `reason_code` field and no `sha256_signature`, it's a structural
rejection. If it has both, it's one of the ten classes above (eight NO_GO/trust/
profile reason codes, or the R-DEV-*/R-PROFILE-* off-Core paths) — a case where
Synapse looked at real content and made a determination, not merely refused
malformed input.

---

## Part 2 — Known assurance gaps: where a technically clean GO may not be fully warranted

**This is the section that actually answers the reviewer's challenge.** Every NO_GO
class above is, definitionally, visible — it comes with a `reason_code`, a signed
record, and (for Core NO_GOs) a Maestro alert. The harder question is the inverse:
under what already-repository-documented conditions does Synapse return a `GO` —
decision `"GO"`, `reason_code: None`, HTTP 200, a signed evidence record indistinguishable
in shape from any other GO — that rests on an input or a wiring gap the system does
not itself flag as uncertain? Every item below is cited to a specific file/line or
`CLAUDE.md` entry; none is invented for this document.

1. **GO Freshness has no implementation at all — a GO's validity is not enforced
   after issuance.** `CLAUDE.md`'s GO Freshness Principle states the model (a GO's
   validity is context-bound: it remains valid exactly as long as the bound claim,
   asset, operator authority, zone, permit context, and state version are
   unchanged) — but this is a *documentation-only* decision, confirmed explicitly
   as not implemented in code. There is no `valid_until` field, no detection
   mechanism for "a bound field changed after issuance," and no revocation
   trigger anywhere in `Verdict` (`src/core/evaluator.py:40-45`) or the persisted
   `AdjudicationRecord` (`src/evidence/emitter.py:90-101`) — `evaluated_at` is the
   only timestamp present, and it is not checked against anything.
   `[Repository-grounded]` `CLAUDE.md` GO Freshness Principle section (lines
   93-101 of the version read for this document) and Open Items entry "GO
   Freshness — unresolved constitutional decisions." **Concretely:** a GO issued
   for a claim is only as fresh as the moment `adjudicate()` ran; nothing in this
   repository re-checks or revokes it if the underlying permit expires, the zone
   becomes hazardous, or the issuer's role is later revoked, during whatever
   window the recipient acts on that GO.

2. **`ClaimPayload.timestamp` is validated for presence and type only — never read
   by any downstream logic.** It is a required `str` field
   (`src/airlock/schemas.py:91`) with no format validator, no timezone-awareness
   check (contrast with `PtwContext.valid_from`/`valid_until`, which *do* get a
   dedicated `_must_be_aware_iso_datetime` validator two fields below it,
   `src/airlock/schemas.py:54-66`), and no comparison anywhere in `src/` against
   `evaluated_at` or "now." `[Repository-grounded]` — confirmed by a repository-wide
   search: no reference to `claim.timestamp` exists outside the schema
   declaration itself. **Concretely:** a claim submitted with a stale, future, or
   entirely fabricated `timestamp` value adjudicates identically to one with an
   accurate one — the field is recorded into the signed `input_payload` for audit
   purposes, but plays no role in the GO/NO_GO decision itself.

3. **`is_design_alteration` is self-declared with no verification logic.** The
   field's own doc comment states this plainly: "Self-declared only, per explicit
   scope — no detection logic, no verification that the flag is honestly raised."
   `[Repository-grounded]` `src/airlock/schemas.py:111-118`. **Concretely:** a
   claim that is in fact a design alteration, but sets `is_design_alteration:
   false`, adjudicates and escalates exactly like a non-alteration claim — QP/QE
   are never notified, and nothing in Synapse detects the discrepancy. A clean GO
   on such a claim would not reflect that a design-alteration reviewer never saw it.

4. **`profile_id_enforcement_enabled` defaults `False` — profile-linked checks are
   opt-in and currently off.** `[Repository-grounded]` `src/config.py:16-28`. While
   this flag is off (the shipped default, and — per the code comment — "matching
   production"), a claim can omit `profile_id` entirely, or submit one that
   resolves to nothing, and proceed to a normal GO/NO_GO adjudication regardless —
   `check_profile_requirement()` records the fact in `rule_trace` but never raises.
   `[Repository-grounded]` `src/airlock/profile_check.py:31-42,96-117`. **Concretely:**
   whatever downstream value a Certified Profile is meant to provide (jurisdiction
   code, base/annex code parameters, PA liability assignment — see item 5) is
   silently absent from every claim today, by default, and nothing in the GO
   output itself signals that absence to an external reader — the `profile_check`
   rule_trace entry says "not enforced," but it is one entry among several,
   `passed: true` like every other passing check, not visually distinct.

5. **`resolve_pa_authority()` exists, is unit-tested, and is never called from any
   live HTTP path.** `[Repository-grounded]` `src/maestro/directory.py:272-301`
   defines the function; a repository-wide search for its call sites finds it
   referenced only in its own module's comments and in
   `tests/test_maestro_directory.py`/`tests/test_profiles_schemas.py` — not in
   `src/airlock/router.py`, `src/frontline/router.py`, or `src/supervisor/router.py`,
   all three of which call `resolve_authority()` (the reason_code/design-alteration
   lookup) but never `resolve_pa_authority()` (the Certified-Profile-keyed PA
   lookup). **Concretely:** the per-project liability assignment mechanism
   `CLAUDE.md` records as "resolved and implemented" (2026-09-03 changelog entry)
   is implemented as a callable function with correct logic and passing tests, but
   is not wired into the actual claim-submission, Frontline, or Supervisor
   response paths — no live claim today ever surfaces a PA binding to any screen
   or Maestro alert, regardless of whether its Certified Profile has an
   `accountable_architect` on file.

6. **`resolve_effective_parameters()` (Certified Profile base/annex code-parameter
   resolution) is built and tested but not called from `adjudicate()` or any rule
   in `src/core/rules.py`.** `[Repository-grounded]` `src/core/evaluator.py:72-90`
   states this explicitly in its own docstring: passing a `certified_profile` into
   `adjudicate()` "produces byte-identical behavior" to omitting it, because
   "nothing in `src/core/rules.py` reads this parameter yet." Confirmed via
   `CLAUDE.md`'s Certified Profile Open Items entry. **Concretely:** whatever
   engineering code parameters (Eurocode + National Annex, or a standalone
   Singapore profile) a project's Certified Profile specifies play no role in any
   rule check today — the plumbing exists, but no rule consults it.

7. **Authority directory has no real registration data anywhere.** Every
   `IssuerRole` row asserting an issuer holds `RTO`/`SA`/`PE`/`QP`/`PA`/`PM`/`QE`
   is a self-asserted database row — there is no connection anywhere in this
   codebase to a real PEB/MOM/IES/ACES registration record. `[Repository-grounded]`
   `CLAUDE.md` Open Items, "PE/QP/PI/PA/PM/SA authority set not bound to real
   registration data" entry (updated 2026-08-18). **Concretely:** the entire
   `GATE_ADMISSIBLE_ROLES` gate (items R-AUTH-02/03, R-ZONE-01, R-PTW-01
   admissibility) checks role *membership* in Synapse's own database, not
   externally-verified credential validity — a clean GO reflects "this issuer's
   `IssuerRole` rows say they hold RTO," not "an external regulator confirms this
   person is a licensed RTO."

8. **`authority_level`/`clearance_level` remain on the schema, accepted and
   enforced at the boundary, but read by nothing.** `[Repository-grounded]`
   `src/airlock/schemas.py:93-105` and `src/core/rules.py:37-47` both carry
   explicit `DEPRECATED` comments dated 2026-08-28: unread since commit `2b26af0`,
   superseded by `GATE_ADMISSIBLE_ROLES` membership, kept only for backward API
   contract compatibility. This is not itself a GO-integrity gap (the
   replacement mechanism, item 7, is what's actually consulted) but is flagged
   here because a caller integrating against `/openapi.json` today would
   reasonably assume a mandatory, schema-enforced field like `authority_level`
   has some bearing on the decision — it has none.

---

## Part 3 — Not repository-grounded, proposed for consideration

Kept explicitly separate from Parts 1–2 per instruction: nothing below is
implemented, flagged as an open item, or otherwise repository-grounded. These are
plausible failure modes this document's author considered while researching the
above, offered only as candidates for a future, explicitly-scoped design pass —
not adopted doctrine, not a finding.

- `[Proposed]` No mechanism exists to detect or flag a claim whose `zone_id`
  references a zone with no sensor coverage at all (`SENSOR_ELIGIBLE_ZONE_FIELDS`
  fields silently fall back to a human-declared value, or a dataclass default,
  with no visible marker in the evidence record distinguishing "verified by
  telemetry" from "nobody ever declared this and it defaulted to False"). This
  is adjacent to, but broader than, the already-grounded `SensorZoneStateRecord`
  vs. `SensorZoneStateRejectionRecord` distinction in Part 1 — those cover
  *attempted* telemetry writes; this would be about *absence* of any telemetry at
  all, which the repository does not currently flag anywhere in a GO's output.
- `[Proposed]` No repository-grounded mechanism cross-checks whether the same
  `issuer_id` submitting a claim is physically capable of being at the claimed
  `zone_id` at the claimed time (e.g. two claims from the same issuer in two
  different zones within an implausible interval). Not investigated in depth for
  this pass; flagged only as a category worth a future, explicitly-scoped
  investigation.

---

## Part 4 — Found but doesn't fit cleanly (flagged, not force-fitted)

- **`CLAUDE.md` is materially behind the current codebase.** The version read for
  this document (310 lines, HEAD `17b3334f...`) documents reason codes through
  `R-ZONE-01`/`R-AUTH-03` and the Certified Profile *schema* (2026-08-11), but has
  no entry at all for: the `profile_id_enforcement_enabled` flag, `R-PROFILE-01`/
  `R-PROFILE-02`, GO Freshness Phase 3a Parts A/B, or the `src/billing/`,
  `src/doctrine/`, `src/ifc_sg/`, or `src/intake/` modules referenced throughout
  the code's own comments. This is not this document's place to fix (out of
  scope, and `CLAUDE.md` is explicitly off-limits for this task), but it means a
  reader relying on `CLAUDE.md` alone as "the constitution" would not learn that
  three of the ten reason codes catalogued in Part 1 (`R-DEV-01`, `R-DEV-02`,
  `R-PROFILE-01`, `R-PROFILE-02` — four, not three) exist at all.
- **`README.md` is also stale** — it states "54/54 tests passing" and describes
  only the Airlock/Core/Evidence/Maestro/Supervisor pipeline as of 2026-07-31; it
  does not mention Frontline, telemetry, profiles, billing, or doctrine at all.
  Not fixed here (out of scope), flagged for whoever next touches it.
- **`R-AUTH-02`'s practical unreachability (Part 1) is a curious middle case** —
  it's a fully-implemented, fully-tested, fully-documented reason code that the
  repository itself confirms cannot currently fire in practice given the current
  rule ordering. It doesn't belong in "assurance gap" (Part 2) since it can't
  produce a wrong GO — a claim that would have hit R-AUTH-02 always gets caught
  earlier, correctly, by R-PTW-01 — but it also isn't a normal catalogue entry
  since "here's what triggers this" is honestly "nothing does, currently." Kept
  in Part 1 with an explicit caveat rather than omitted or misfiled into Part 2.

---

## Part 5 — Governance dimensions not yet addressed

**This section documents an absence, not a bug or an assurance gap.** Parts 1–4
above all describe things that are *built* — reason codes that fire, GOs that
issue, gaps in how thoroughly an implemented mechanism is verified. The three
items below are different in kind: they are not implemented anywhere in this
codebase, partially or otherwise. This is scope that has not been built yet, not
an oversight being surfaced here for the first time as a surprise.

Confirmed by a repository-wide, case-insensitive search of `src/` (all 60
Python files across `airlock/`, `billing/`, `core/`, `doctrine/`, `evidence/`,
`frontline/`, `ifc_sg/`, `intake/`, `maestro/`, `profiles/`, `supervisor/`,
`telemetry/`) for privacy, retention, and classification terminology:

- **Privacy rules.** No match for `privacy`, `PII`, `personal data`, `GDPR`,
  `data subject`, `anonymiz*`, or `pseudonymiz*` anywhere in `src/`. There is no
  concept anywhere in the schema or adjudication logic of a field being
  personal data, no consent mechanism, and no distinction between personal and
  non-personal fields in any `ClaimPayload`, `CertifiedProfile`, or evidence
  record.
- **Retention rules.** No match for `retention`, `purge`, `TTL`/`time-to-live`,
  or scheduled deletion/archival of records anywhere in `src/`. Every signed
  evidence record (`AdjudicationRecord`, `SensorZoneStateRejectionRecord`,
  `ProfileRejectionRecord`, etc.) is written with no expiry, no purge job, and
  no retention-period field. The only tangential hit was `expire_on_commit=False`
  in `src/core/repository.py:28` — a SQLAlchemy session-lifecycle setting with
  no relationship to data retention policy.
- **Classification rules.** No match for `sensitivity`, `confidential`, `data
  category`, or `label level` anywhere in `src/`. The word "classification" and
  the function name `classify_authority_failure()` do appear (`src/core/rules.py`,
  `src/core/evaluator.py`, `src/airlock/profile_check.py`,
  `src/intake/adapters/eptw.py`), and `src/airlock/schemas.py:15-28` defines a
  `WorkType` enum described in its own docstring as a "work classification" —
  but both are unrelated to data-governance classification: `classify_authority_
  failure()` is a pure adjudication helper that names which authority-failure
  reason code applies, and `WorkType` categorizes the physical work being
  claimed (excavation, lifting, hot work, etc.) for ePTW gating, not the
  sensitivity or handling requirements of the data itself. Flagged here rather
  than silently excluded, per instruction, since the naming overlap could
  otherwise read as a false confirmation that classification is addressed.

No design or implementation is proposed here for any of the three. This entry
exists so a reader relying on this catalogue does not conclude, by omission,
that these dimensions were considered and found adequately covered.
