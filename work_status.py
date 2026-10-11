"""Allowlist for work-status values supplied by an approved upstream process.

This module only normalizes an existing status field. It does not infer fitness
for work from sensor readings or generate a status on its own.
"""

STATUS_LABELS = {
    "good_to_work": "Good to work",
    "needs_attention": "Needs attention",
    "not_fit": "Not fit",
}

_ALIASES = {
    "good to work": "good_to_work",
    "good_to_work": "good_to_work",
    "needs attention": "needs_attention",
    "need attention": "needs_attention",
    "needs_attention": "needs_attention",
    "need_attention": "needs_attention",
    "not fit": "not_fit",
    "not_fit": "not_fit",
    "not well to work": "not_fit",
    "not_well_to_work": "not_fit",
}


def canonical_work_status(value):
    """Return one allowlisted status code; unknown/missing values become None."""
    if not isinstance(value, str):
        return None
    key = " ".join(value.strip().casefold().replace("-", " ").replace("_", " ").split())
    if not key:
        return None
    return _ALIASES.get(key)


def display_work_status(value):
    """Return a human-readable allowlisted label or the safe fallback."""
    canonical = canonical_work_status(value)
    return STATUS_LABELS.get(canonical, "Not assessed")
