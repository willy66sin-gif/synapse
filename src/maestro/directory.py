"""
Deterministic authority-escalation directory.

Per the Escalation Ownership Principle (CLAUDE.md): who owns
escalating a rejected claim is determined by the adjudicated failure
reason (reason_code), not by the work activity that was attempted
(claim_type/work_type) -- a PTW failure in a zone and an authority
failure in that same zone are two different people's problem, even
though they happened in the same place.

Zero I/O, zero external calls -- a pure in-memory lookup, same
discipline as src/core/rules.py and src/supervisor/logic.py.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class AuthorityBinding:
    binding_id: str
    role: str
    contact_id: Optional[str] = None


# Fixed system constant, not per-binding data -- every escalation
# points at the same real override endpoint regardless of which
# authority is resolved. Deliberately not a field on AuthorityBinding.
SUPERVISOR_OVERRIDE_URL = "https://synapse.local/supervisor/override"


# Starts with exactly one entry: the catch-all default. Real
# (zone_id, reason_code) and ("*", reason_code) entries get added here
# as actual site authorities are identified -- no placeholder names,
# phone numbers, or fabricated zone/role data belongs here ahead of
# that; contact_id stays None until a real contact channel exists.
DIRECTORY_MAP: dict[tuple[Optional[str], Optional[str]], AuthorityBinding] = {
    ("*", "*"): AuthorityBinding("BIND-999", "General Duty Officer", None),
}


def resolve_authority(zone_id: Optional[str], reason_code: Optional[str]) -> AuthorityBinding:
    """
    Precedence, most to least specific:
      1. (zone_id, reason_code)  -- specific match
      2. ("*", reason_code)      -- global reason-code default
      3. ("*", "*")               -- catch-all system default

    The catch-all is required to exist in DIRECTORY_MAP; this function
    fails closed (raises) rather than returning an unresolved binding
    if it's ever missing, instead of silently falling through to None.
    """
    for key in ((zone_id, reason_code), ("*", reason_code), ("*", "*")):
        binding = DIRECTORY_MAP.get(key)
        if binding is not None:
            return binding

    raise KeyError(
        "No AuthorityBinding matched, not even the ('*', '*') catch-all -- DIRECTORY_MAP is misconfigured."
    )
