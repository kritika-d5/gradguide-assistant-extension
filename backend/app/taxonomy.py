"""Tag taxonomy: which field and career tags count as related.

Exact matches earn full credit and related tags earn partial credit, so "data scientist" is not
treated as unrelated to "ML engineer". Edit the groups here as the catalogue grows.
"""
from __future__ import annotations

RELATED_GROUPS: list[set[str]] = [
    # fields
    {"data_science", "ml", "statistics"},
    {"data_science", "business_analytics"},
    {"computer_science", "software", "ml", "cybersecurity"},
    {"robotics", "mechanical", "electrical"},
    {"business_analytics", "management"},
    # careers
    {"data_scientist", "ml_engineer", "data_analyst"},
    {"software_engineer", "ml_engineer", "security_analyst"},
    {"business_analyst", "data_analyst", "product_manager"},
    {"robotics_engineer", "mechanical_engineer"},
]

EXACT = 1.0
RELATED = 0.5
NO_MATCH_FLOOR = 0.2  # a complete mismatch is a strong negative, but not a dealbreaker


def related(a: str, b: str) -> bool:
    return any(a in g and b in g for g in RELATED_GROUPS)


def tag_similarity(wanted: list[str], offered: list[str]) -> tuple[float, set[str], set[str]]:
    """Average best credit per wanted tag, mapped onto [NO_MATCH_FLOOR, 1].

    Returns (score, exact matches, wanted tags matched only through a related tag).
    """
    wanted_set, offered_set = set(wanted), set(offered)
    exact = wanted_set & offered_set
    near = {w for w in wanted_set - exact if any(related(w, o) for o in offered_set)}
    raw = (EXACT * len(exact) + RELATED * len(near)) / len(wanted_set)
    return NO_MATCH_FLOOR + (1 - NO_MATCH_FLOOR) * raw, exact, near
