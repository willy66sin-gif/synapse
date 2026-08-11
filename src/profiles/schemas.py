"""
Certified Profile schemas — the two lineage patterns a jurisdiction's
adopted code can take.

STANDALONE: the jurisdiction has its own self-contained code, no
shared base (precedent: Singapore's current profile — see
CLAUDE.md's Open Items on multi-regulator rulesets for the *separate*,
not-yet-designed problem of multiple regulators within one
jurisdiction; this module doesn't touch that).

BASE_ANNEX: the jurisdiction adopts a shared base code with local
parameters layered on top (precedent: EN Eurocode + National Annex
per country). `base_ref` names exactly which base profile and which
pinned version it annexes.

`lineage` is explicit, not inferred from `base_ref`'s presence —
fail-closed doctrine means an ambiguous combination (e.g. lineage
BASE_ANNEX with no base_ref, or the reverse) gets rejected outright by
the validator below rather than guessed at.
"""
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, model_validator


class ProfileLineage(str, Enum):
    STANDALONE = "STANDALONE"
    BASE_ANNEX = "BASE_ANNEX"


class BaseProfileRef(BaseModel):
    """
    Pointer to the shared base code a BASE_ANNEX profile annexes.

    base_profile_version is pinned, not a floating "latest" reference —
    src/core/profile_resolution.py's resolve_effective_parameters is a
    pure function (same discipline as src/core/rules.py), and resolving
    against a mutable base would make its output depend on when it's
    called, not just on what's passed in.
    """

    model_config = ConfigDict(extra="forbid")

    base_profile_id: str
    base_profile_version: str


class CertifiedProfile(BaseModel):
    """
    A jurisdiction's certified code profile — either self-contained
    (STANDALONE) or an annex on a shared base (BASE_ANNEX).

    `parameters` holds this profile's own values only: the full
    parameter set for STANDALONE, or just the local overrides/additions
    for BASE_ANNEX — resolving the two into an effective parameter set
    is deliberately not this schema's job (see
    src/core/profile_resolution.py's module docstring for why that
    belongs in Core, not here).
    """

    model_config = ConfigDict(extra="forbid")

    profile_id: str
    jurisdiction_code: str
    version: str
    lineage: ProfileLineage
    base_ref: Optional[BaseProfileRef] = None
    parameters: dict[str, Any]

    @model_validator(mode="after")
    def _lineage_matches_base_ref(self) -> "CertifiedProfile":
        has_base = self.base_ref is not None
        if self.lineage == ProfileLineage.BASE_ANNEX and not has_base:
            raise ValueError("BASE_ANNEX profile must set base_ref")
        if self.lineage == ProfileLineage.STANDALONE and has_base:
            raise ValueError("STANDALONE profile must not set base_ref")
        return self
