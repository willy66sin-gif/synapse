"""
Certified profile parameter resolution.

Pure function only — no database side-effects, no I/O — same
discipline src/core/rules.py's checks and src/core/evaluator.py's
adjudicate() already follow: this module only ever sees already-fetched
CertifiedProfile records (src/profiles/repository.py's
fetch_certified_profile does the actual lookup one layer up), never
resolves them itself.

Deliberately separate from CLAUDE.md's Open Items line-115
"Multi-regulator ruleset architecture" item (multiple regulators — LTA,
BCA, MOM — each running an independent ruleset within one jurisdiction,
with an aggregate fail-closed verdict). That is a different problem —
which regulator's rules apply and how conflicting verdicts across
regulators aggregate — and remains undesigned. This module only merges
one jurisdiction's own base+annex parameter layers into a single
effective set; it has no concept of "regulator" or "aggregate verdict"
and doesn't touch that item.

Two distinct failure modes, deliberately not collapsed into one error
type (mirrors src/telemetry/trust.py's DeviceNotRegisteredError vs.
TelemetrySignatureInvalidError distinction):
  - BaseProfileMissingError: profile.lineage is BASE_ANNEX but no base
    was supplied. The caller didn't fetch (or couldn't find) the base
    profile.base_ref names.
  - BaseProfileMismatchError: a base WAS supplied, but its identity
    doesn't match what profile.base_ref actually pins — a caller-side
    wiring bug (wrong profile fetched), not a missing-data case.
"""
from typing import Any, Optional

from src.profiles.schemas import CertifiedProfile, ProfileLineage


class BaseProfileMissingError(LookupError):
    """profile.lineage is BASE_ANNEX but no base CertifiedProfile was supplied."""


class BaseProfileMismatchError(ValueError):
    """The supplied base's (profile_id, version) doesn't match profile.base_ref's pinned reference."""


def resolve_effective_parameters(profile: CertifiedProfile, base: Optional[CertifiedProfile]) -> dict[str, Any]:
    """
    STANDALONE: returns profile.parameters as-is — there is no base to
    merge.

    BASE_ANNEX: returns base.parameters overlaid with profile.parameters
    (annex values win on key collision, base fills the rest) — after
    confirming `base` is actually the pinned (base_profile_id,
    base_profile_version) profile.base_ref names, not just some other
    profile the caller happened to pass in.
    """
    if profile.lineage == ProfileLineage.STANDALONE:
        return dict(profile.parameters)

    # profile.lineage == BASE_ANNEX here — schema validation
    # (CertifiedProfile._lineage_matches_base_ref) already guarantees
    # base_ref is set whenever lineage is BASE_ANNEX.
    if base is None:
        raise BaseProfileMissingError(
            f"Profile '{profile.profile_id}' is BASE_ANNEX and requires base "
            f"'{profile.base_ref.base_profile_id}' (version {profile.base_ref.base_profile_version}), "
            f"but no base profile was supplied."
        )

    if base.profile_id != profile.base_ref.base_profile_id or base.version != profile.base_ref.base_profile_version:
        raise BaseProfileMismatchError(
            f"Profile '{profile.profile_id}' pins base "
            f"'{profile.base_ref.base_profile_id}' version '{profile.base_ref.base_profile_version}', "
            f"but the supplied base is '{base.profile_id}' version '{base.version}'."
        )

    return {**base.parameters, **profile.parameters}
