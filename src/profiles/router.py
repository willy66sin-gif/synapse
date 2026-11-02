"""
CertifiedProfile creation endpoint (Step 2, 2026-10-11,
Willy-authorized).

New: no prior endpoint existed for src/profiles/ -- the only thing
that ever wrote a certified_profiles row was scripts/seed_dev_data.py.
Mirrors src/doctrine/router.py's POST /doctrine/submissions shape
(validate -> persist -> emit hashed evidence -> persist evidence). It
does not adjudicate anything.

Create only. There is no update or delete route: profile_id is the
table's primary key, so a second POST for the same profile_id is a
409, not a new version. Publishing a changed code means a new
profile_id; base_ref pins (base_profile_id, base_profile_version), so
existing annexes stay on the base they named until re-issued.

BASE_ANNEX checks, done here because they need a database read the
schema cannot do. Both fail closed with 422 and persist nothing:
  1. The base must already exist, at exactly the pinned version --
     same two conditions src/core/profile_resolution.py's
     BaseProfileMissingError/BaseProfileMismatchError guard at
     resolution time, caught at write time instead so an unresolvable
     annex is never stored.
  2. The base must belong to the same project as the annex, or be a
     GLOBAL shared code (src/profiles/schemas.py's GLOBAL_PROJECT_ID).
     One project's profile annexing another project's profile would be
     a cross-project reference, the thing src/airlock/project_check.py
     exists to refuse.

A GLOBAL profile is created through this same route, by declaring
project_id "GLOBAL" -- no separate endpoint, no nullable field.

Access: this route has no caller authentication, which is the
existing, documented posture of the whole API, not something this
route introduces -- see README.md's "single trusted-reviewer local
instance" section: project_id is a data-scoping boundary, not access
control. Nothing about that is changed or decided here.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.repository import get_db_session
from src.evidence.emitter import emit_certified_profile_evidence
from src.profiles.repository import (
    fetch_certified_profile,
    persist_certified_profile,
    persist_certified_profile_receipt,
)
from src.profiles.schemas import GLOBAL_PROJECT_ID, CertifiedProfile

router = APIRouter(prefix="/profiles", tags=["profiles"])


@router.post("")
async def create_certified_profile(
    profile: CertifiedProfile, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """
    FastAPI + Pydantic v2 already enforce fail-closed validation here --
    a malformed body (missing project_id or any other required field,
    unknown field, or a lineage/base_ref mismatch) 422s before this
    line runs.
    """
    if profile.base_ref is not None:
        base = await fetch_certified_profile(session, profile.base_ref.base_profile_id)
        if base is None or base.version != profile.base_ref.base_profile_version:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"base_ref names profile '{profile.base_ref.base_profile_id}' at version "
                    f"'{profile.base_ref.base_profile_version}', which does not exist."
                ),
            )
        if base.project_id not in (GLOBAL_PROJECT_ID, profile.project_id):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"base profile '{base.profile_id}' belongs to project '{base.project_id}', not to "
                    f"'{profile.project_id}' and not to the shared '{GLOBAL_PROJECT_ID}' scope."
                ),
            )

    try:
        await persist_certified_profile(session, profile)
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"A certified profile with profile_id '{profile.profile_id}' already exists.",
        ) from exc

    evidence = emit_certified_profile_evidence(profile.model_dump(mode="json"))
    await persist_certified_profile_receipt(session, evidence)

    return evidence
