"""
ePTW / permit-system claim-source adapter.

Stub-shaped the same way src/maestro/adapters/whatsapp.py and
telegram.py are: parsing is real, transport is not this module's job
(see src/intake/client.py), and the raw record shape assumed below is
a placeholder contract, not a confirmed vendor API -- no specific ePTW
/ permit-management product was named when this adapter was scoped.
Field *names* in ASSUMED_RAW_RECORD_SHAPE below will need to change
once a real source system is identified; the translation logic and its
fail-closed posture should not.

Crosswalk-registry finding (2026-08-09): checked src/core/repository.py,
src/core/models.py, src/maestro/directory.py, and
scripts/seed_dev_data.py for any registry mapping an external system's
issuer/zone identifiers to Synapse's own issuer_id / zone_id /
clearance_level. None existed at the time. AuthorizedIssuer (Postgres)
was keyed directly by Synapse-native issuer_id with no external-ID
column; Redis zone state was keyed directly by Synapse-native zone_id
with no external-location-code layer either.

Crosswalk container built (2026-08-09, follow-up): src/intake/models.py's
IdentityCrosswalkEntry and src/intake/repository.py's resolve_issuer/
resolve_zone now exist as the real lookup interface -- zone_id and
issuer_id below go through it. The table still ships empty (no real or
placeholder mapping data -- see src/intake/models.py's docstring for
why), so every lookup still correctly fails today, now via
CrosswalkMissError raised through the real interface instead of a
hardcoded NotImplementedError stub.

authority_level is NOT resolved via the same table: it isn't an
identity lookup (external_id -> a different but equally-opaque Synapse
ID) -- it's a small, closed vocabulary translation (source role/
clearance name -> Synapse's integer clearance_level), structurally
closer to WORK_TYPE_MAP below than to IdentityCrosswalkEntry. No real
source vocabulary is known yet, so it stays NotImplementedError, same
as ptw_context.status and ptw_context.issuer_id (approver) -- both
still explicitly held from the prior pass, unrelated to this one.
"""
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.airlock.schemas import ClaimPayload, PtwContext, WorkType
from src.core.rules import HIGH_RISK_WORK_TYPES
from src.intake.adapters.base import ClaimSourceAdapter
from src.intake.repository import resolve_issuer, resolve_zone

# Example only -- the raw payload shape this adapter currently parses.
# Not sourced from any confirmed vendor API; update field names once a
# real ePTW/permit system is identified.
ASSUMED_RAW_RECORD_SHAPE = {
    "request_id": "EPTW-2026-000123",
    "submitted_at": "2026-08-09T10:15:00+00:00",
    "permit_type": "HOT_WORK",
    "activity_code": "HOT_CUTTING",
    "location_code": "SITE-A-ZONE-3",
    "requester_ref": "EXT-USR-4471",
    "requester_clearance": "AREA_SUPERVISOR",
    "extra": {},
    "permit": {
        "permit_number": "PTW-HW-000456",
        "status": "Approved by AH",
        "valid_from": "2026-08-09T06:00:00+00:00",
        "valid_until": "2026-08-09T18:00:00+00:00",
        "approver_ref": "EXT-USR-9002",
    },
}

# Closed vocabulary: source permit_type code -> Synapse WorkType. Used
# once per translate() call and the single resulting value is reused
# for both ClaimPayload.work_type and ptw_context.permit_type, per the
# approved design's own warning that two independent lookups risk
# drifting apart -- Rule 0 (verify_ptw_precondition) requires the two
# to compare equal.
WORK_TYPE_MAP: dict[str, WorkType] = {
    "NOMINAL_CIVIL": WorkType.NOMINAL_CIVIL,
    "EXCAVATION": WorkType.EXCAVATION,
    "LIFTING": WorkType.LIFTING,
    "HOT_WORK": WorkType.HOT_WORK,
    "CONFINED_SPACE": WorkType.CONFINED_SPACE,
}

# The only action_type value anything in src/core/ actually reads
# (check_zone_safety's "LIFT_OPERATION" comparison). Recognizing source
# codes that mean "this is a lift" and normalizing them to that one
# literal is in scope; inventing any *other* new action_type vocabulary
# is not (action_type has no established taxonomy elsewhere in this
# codebase -- see src/core/rules.py's classify_authority_failure
# docstring -- so asserting one here would be out of this adapter's
# lane, not a translation).
_LIFT_ACTIVITY_CODES = frozenset({"LIFTING_OPERATION", "CRANE_LIFT", "HEAVY_LIFT"})


def _map_work_type(source_permit_type: str) -> WorkType:
    try:
        return WORK_TYPE_MAP[source_permit_type]
    except KeyError:
        raise ValueError(
            f"Unrecognized source permit_type '{source_permit_type}' -- no WorkType mapping for it "
            f"in WORK_TYPE_MAP. Refusing to guess; add a confirmed entry instead."
        ) from None


def _map_action_type(source_activity_code: str) -> str:
    if source_activity_code in _LIFT_ACTIVITY_CODES:
        return "LIFT_OPERATION"
    return source_activity_code


def _require_aware_iso_datetime(value: str, field_name: str) -> str:
    """Reject naive/local timestamps rather than guessing a timezone (holding decision, per design)."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(
            f"{field_name} '{value}' is timezone-naive. Refusing to guess a timezone -- source "
            f"system must supply a UTC offset (e.g. a trailing 'Z' or '+00:00')."
        )
    return value


class CrosswalkMissError(LookupError):
    """
    Raised when translate() needs a crosswalk entry that isn't on file.
    Distinct from a mapping *bug* (e.g. _map_work_type's ValueError for
    an unrecognized closed-vocabulary value): the lookup mechanism is
    real here, the row just doesn't exist -- expected, current state
    for every external_id, since IdentityCrosswalkEntry ships empty.
    """


async def _require_crosswalk_zone(session: AsyncSession, source_system: str, external_id: str) -> str:
    synapse_id = await resolve_zone(session, source_system, external_id)
    if synapse_id is None:
        raise CrosswalkMissError(
            f"No zone crosswalk entry for source_system={source_system!r} external_id={external_id!r}. "
            f"src/intake/models.py's identity_crosswalk table has no matching row."
        )
    return synapse_id


async def _require_crosswalk_issuer(session: AsyncSession, source_system: str, external_id: str) -> str:
    synapse_id = await resolve_issuer(session, source_system, external_id)
    if synapse_id is None:
        raise CrosswalkMissError(
            f"No issuer crosswalk entry for source_system={source_system!r} external_id={external_id!r}. "
            f"src/intake/models.py's identity_crosswalk table has no matching row."
        )
    return synapse_id


def _blocked_authority_level(_raw_record: dict) -> int:
    raise NotImplementedError(
        "authority_level crosswalk not implemented: no registry mapping the external system's "
        "role/clearance vocabulary to Synapse's integer clearance_level was found in this "
        "codebase (see this module's docstring). Populating this with a guessed mapping was "
        "explicitly ruled out -- resolve the crosswalk question before implementing this."
    )


def _blocked_permit_status(_permit: dict) -> str:
    raise NotImplementedError(
        "ptw_context.status mapping not implemented: deciding which source permit statuses "
        "count as equivalent to Rule 0's required literal 'APPROVED' is a judgment call about "
        "permit validity, not a translation -- explicitly held pending a decision by whoever "
        "owns Rule 0's semantics (src/core/rules.py's verify_ptw_precondition). Do not guess here."
    )


def _blocked_ptw_approver_id(_permit: dict) -> str:
    raise NotImplementedError(
        "ptw_context.issuer_id (permit approver) mapping not implemented: it is unconfirmed "
        "whether the source ePTW system actually distinguishes a requester from an approver, "
        "and this field is distinct from the top-level ClaimPayload.issuer_id (claim submitter). "
        "Explicitly held pending that confirmation. Do not guess which party this is."
    )


class EptwAdapter(ClaimSourceAdapter):
    source_name = "eptw"

    async def translate(self, raw_record: dict, session: AsyncSession) -> ClaimPayload:
        claim_id = raw_record["request_id"]
        timestamp = _require_aware_iso_datetime(raw_record["submitted_at"], "submitted_at")
        work_type = _map_work_type(raw_record["permit_type"])
        action_type = _map_action_type(raw_record.get("activity_code", ""))
        payload_data = dict(raw_record.get("extra", {}))

        # Every field validated above is fully implemented. Resolve and
        # validate the rest of the *implemented* surface -- including
        # the permit's own timestamps -- before touching the crosswalk
        # or the still-held fields below, so a malformed-but-otherwise-
        # mappable record fails on its actual problem, not on an
        # unrelated lookup miss.
        permit = raw_record.get("permit", {}) if work_type in HIGH_RISK_WORK_TYPES else None
        ptw_id = permit["permit_number"] if permit is not None else None
        valid_from = (
            _require_aware_iso_datetime(permit["valid_from"], "permit.valid_from") if permit is not None else None
        )
        valid_until = (
            _require_aware_iso_datetime(permit["valid_until"], "permit.valid_until") if permit is not None else None
        )

        # Real lookup interface (src/intake/repository.py). The table
        # ships empty, so this still correctly raises CrosswalkMissError
        # for every input today -- through the real interface now,
        # not a hardcoded stub.
        zone_id = await _require_crosswalk_zone(session, self.source_name, raw_record["location_code"])
        issuer_id = await _require_crosswalk_issuer(session, self.source_name, raw_record["requester_ref"])

        # Still held -- see module docstring. Raises NotImplementedError
        # immediately; nothing below this point currently executes.
        authority_level = _blocked_authority_level(raw_record)

        ptw_context = None
        if permit is not None:
            ptw_context = PtwContext(
                ptw_id=ptw_id,
                status=_blocked_permit_status(permit),
                valid_from=valid_from,
                valid_until=valid_until,
                permit_type=work_type,
                zone_id=zone_id,
                issuer_id=_blocked_ptw_approver_id(permit),
            )

        return ClaimPayload(
            claim_id=claim_id,
            timestamp=timestamp,
            issuer_id=issuer_id,
            authority_level=authority_level,
            zone_id=zone_id,
            action_type=action_type,
            payload_data=payload_data,
            work_type=work_type,
            ptw_context=ptw_context,
        )
