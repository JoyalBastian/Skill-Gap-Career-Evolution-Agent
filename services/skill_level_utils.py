"""Map skill proficiency gaps to course levels (beginner / intermediate / advanced)."""
from __future__ import annotations

VALID_COURSE_LEVELS = ("beginner", "intermediate", "advanced")


def _clip_prof(value, lo: int = 0, hi: int = 5) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        v = 0
    return max(lo, min(hi, v))


def normalize_course_level(raw: str | None) -> str:
    level = (raw or "beginner").strip().lower()
    if level in ("expert", "experts", "expertise"):
        return "advanced"
    if level not in VALID_COURSE_LEVELS:
        return "beginner"
    return level


def next_level(level: str) -> str | None:
    normalized = normalize_course_level(level)
    if normalized == "beginner":
        return "intermediate"
    if normalized == "intermediate":
        return "advanced"
    return None


def proficiency_to_start_level(user_prof: int, required_prof: int) -> str:
    """
    Course level the user should start with for this skill gap.
    Considers current proficiency and distance to the career requirement.
    """
    user_prof = _clip_prof(user_prof)
    required_prof = _clip_prof(required_prof, lo=1)
    gap = required_prof - user_prof

    if user_prof <= 1 or gap >= 3:
        return "beginner"
    if user_prof <= 3:
        return "intermediate"
    return "advanced"


def compute_gap_levels(
    user_prof: int,
    required_prof: int,
) -> tuple[str, str | None]:
    """Return (recommended_start_level, next_level) for course generation."""
    start = proficiency_to_start_level(user_prof, required_prof)
    if start == "advanced":
        return start, None
    return start, next_level(start)


def enrich_gap_context(gap_context: dict) -> dict:
    """Ensure gap dict has proficiency fields and computed course levels."""
    user_prof = _clip_prof(gap_context.get("user_proficiency", 0))
    req_prof = _clip_prof(
        gap_context.get("required_proficiency") or gap_context.get("importance") or 3,
        lo=1,
    )
    start, nxt = compute_gap_levels(user_prof, req_prof)
    if gap_context.get("recommended_start_level"):
        start = normalize_course_level(gap_context["recommended_start_level"])
    if "next_level" in gap_context:
        raw_next = gap_context["next_level"]
        nxt = normalize_course_level(raw_next) if raw_next else None
    elif nxt is None and start != "advanced":
        nxt = next_level(start)

    return {
        **gap_context,
        "user_proficiency": user_prof,
        "required_proficiency": req_prof,
        "recommended_start_level": start,
        "next_level": nxt,
    }


def level_display_label(level: str) -> str:
    normalized = normalize_course_level(level)
    labels = {
        "beginner": "Beginner",
        "intermediate": "Intermediate",
        "advanced": "Expert",
    }
    return labels.get(normalized, normalized.title())
