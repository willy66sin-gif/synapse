"""
Claim-source adapter interface.

Intake-side counterpart to src/maestro/adapters/base.py's
ChannelAdapter, and structured the same way: an ABC that every
concrete external-system adapter implements, so the source is
interchangeable per CLAUDE.md's System Principle ("Core decides,
Maestro delivers") applied to ingestion instead of delivery.

ChannelAdapter is two-directional (send_alert + parse_inbound_status_
query); a claim-source adapter is one-directional -- external record
in, ClaimPayload out -- so there is exactly one abstract method here.

Adapters only ever translate one external record into a ClaimPayload.
They must never:
  - import anything from src/core/ or src/airlock/ beyond the types
    needed to construct a ClaimPayload (ClaimPayload, WorkType,
    PtwContext) -- no rule logic, no adjudication, no repository access
  - validate anything beyond what ClaimPayload's own schema already
    enforces -- Airlock's fail-closed gate is the single source of
    truth for schema validity, not a second copy of it here
  - make a decision -- e.g. no judging whether a permit is genuinely
    valid (that is verify_ptw_precondition's job, not this adapter's)

Transport (POSTing the translated ClaimPayload to POST /airlock/claims)
is deliberately not part of this interface -- see src/intake/client.py.

translate() is async and takes a session (2026-08-09, identity
crosswalk container): a real translation may need to resolve an
identity crosswalk lookup (src/intake/repository.py's resolve_issuer/
resolve_zone) to find the Synapse-native ID a claim needs. That's
still translation, not a decision -- the same category as parsing a
date string -- it just needs a database round-trip instead of pure
string logic, the same reason src/core/repository.py's fetch
functions exist separately from src/core/rules.py's pure checks.
"""
from abc import ABC, abstractmethod

from sqlalchemy.ext.asyncio import AsyncSession

from src.airlock.schemas import ClaimPayload


class ClaimSourceAdapter(ABC):
    source_name: str

    @abstractmethod
    async def translate(self, raw_record: dict, session: AsyncSession) -> ClaimPayload:
        """Map one external system record into a ClaimPayload, resolving any identity crosswalk lookups it needs via session."""
