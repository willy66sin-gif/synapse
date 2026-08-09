"""
Claim-source adapter tests.

EptwAdapter.translate() cannot currently produce a complete
ClaimPayload for any input -- zone_id and issuer_id now resolve
through the real identity crosswalk (src/intake/repository.py), but
that table ships empty (see src/intake/models.py's docstring), and
authority_level/ptw_context.status/ptw_context.issuer_id remain
explicitly held. So "happy path" here means: every field that *is*
implemented validates correctly, and translate() then raises
CrosswalkMissError at the zone_id lookup specifically -- through the
real repository interface, against a stub session standing in for an
empty table, not a hardcoded stub inside the adapter. That is today's
correct behavior, not a bug, and these tests pin it down.
"""
import pytest

from src.intake.adapters.eptw import (
    EptwAdapter,
    CrosswalkMissError,
    _blocked_authority_level,
    _blocked_permit_status,
    _blocked_ptw_approver_id,
    _map_action_type,
)


class _EmptyCrosswalkSession:
    """Stands in for the real, empty identity_crosswalk table -- every lookup misses, same as production today."""

    async def execute(self, _stmt):
        return self

    def scalar_one_or_none(self):
        return None


def _raw_record(**overrides) -> dict:
    base = {
        "request_id": "EPTW-2026-000123",
        "submitted_at": "2026-08-09T10:15:00+00:00",
        "permit_type": "HOT_WORK",
        "activity_code": "HOT_CUTTING",
        "location_code": "SITE-A-ZONE-3",
        "requester_ref": "EXT-USR-4471",
        "requester_clearance": "AREA_SUPERVISOR",
        "extra": {"crew_size": 4},
        "permit": {
            "permit_number": "PTW-HW-000456",
            "status": "Approved by AH",
            "valid_from": "2026-08-09T06:00:00+00:00",
            "valid_until": "2026-08-09T18:00:00+00:00",
            "approver_ref": "EXT-USR-9002",
        },
    }
    base.update(overrides)
    return base


# --- translate(): implemented fields validate before the crosswalk lookup ---


@pytest.mark.asyncio
async def test_translate_blocks_on_zone_crosswalk_miss_once_other_fields_validate():
    """
    'Happy path' for a partially-implemented adapter: work_type,
    timestamp, action_type, and payload_data all resolve cleanly, and
    the raise happens exactly at the zone_id crosswalk lookup -- through
    the real src/intake/repository.py interface (an empty table, same
    as production), not a hardcoded stub. Proves the block is live.
    """
    with pytest.raises(CrosswalkMissError, match="No zone crosswalk entry"):
        await EptwAdapter().translate(_raw_record(), _EmptyCrosswalkSession())


@pytest.mark.asyncio
async def test_translate_rejects_unknown_work_type_before_reaching_the_crosswalk():
    with pytest.raises(ValueError, match="Unrecognized source permit_type 'DEMOLITION'"):
        await EptwAdapter().translate(_raw_record(permit_type="DEMOLITION"), _EmptyCrosswalkSession())


@pytest.mark.asyncio
async def test_translate_rejects_naive_timestamp_before_reaching_the_crosswalk():
    with pytest.raises(ValueError, match="submitted_at .* is timezone-naive"):
        await EptwAdapter().translate(
            _raw_record(submitted_at="2026-08-09T10:15:00"), _EmptyCrosswalkSession()
        )


@pytest.mark.asyncio
async def test_translate_rejects_naive_permit_validity_window():
    with pytest.raises(ValueError, match="permit.valid_from .* is timezone-naive"):
        await EptwAdapter().translate(
            _raw_record(permit={
                "permit_number": "PTW-HW-000456",
                "status": "Approved by AH",
                "valid_from": "2026-08-09T06:00:00",
                "valid_until": "2026-08-09T18:00:00+00:00",
                "approver_ref": "EXT-USR-9002",
            }),
            _EmptyCrosswalkSession(),
        )


@pytest.mark.asyncio
async def test_translate_still_blocks_for_nominal_civil_on_zone_crosswalk():
    """
    NOMINAL_CIVIL never builds a PtwContext, so it never touches the
    still-held permit status/approver fields -- but zone_id is required
    on every ClaimPayload regardless of work_type, so it still hits the
    (empty) crosswalk. Confirms the crosswalk block applies uniformly,
    not just to high-risk work types.
    """
    with pytest.raises(CrosswalkMissError, match="No zone crosswalk entry"):
        await EptwAdapter().translate(
            _raw_record(permit_type="NOMINAL_CIVIL", permit={}), _EmptyCrosswalkSession()
        )


# --- action_type mapping ---


def test_map_action_type_normalizes_recognized_lift_codes():
    assert _map_action_type("CRANE_LIFT") == "LIFT_OPERATION"


def test_map_action_type_passes_through_unrecognized_codes_unchanged():
    """No established action_type vocabulary exists beyond LIFT_OPERATION -- see module docstring."""
    assert _map_action_type("HOT_CUTTING") == "HOT_CUTTING"


# --- fields still explicitly held: confirm genuinely unimplemented, not stubbed with a guess ---


@pytest.mark.parametrize(
    "blocked_fn",
    [_blocked_authority_level, _blocked_permit_status, _blocked_ptw_approver_id],
)
def test_held_fields_raise_not_implemented_rather_than_guessing(blocked_fn):
    with pytest.raises(NotImplementedError):
        blocked_fn({})
