"""Shared skill row helpers for AI pipeline services."""
from __future__ import annotations

import re

from django.db import IntegrityError
from django.utils.text import slugify

from apps.skills.models import Skill

_JOB_TITLE_SUFFIXES = (
    " engineer",
    " developer",
    " analyst",
    " architect",
    " manager",
    " specialist",
    " consultant",
    " administrator",
    " scientist",
)

_ACRONYM_RE = re.compile(r"^[A-Z0-9]{2,6}$")


def humanize_skill_name(name: str) -> str:
    """Turn slug-like labels into readable skill names."""
    name = (name or "").strip()
    if not name:
        return name
    if _ACRONYM_RE.match(name):
        return name
    looks_like_slug = (
        "_" in name
        or (name == name.lower() and "-" in name and " " not in name)
        or (name == name.lower() and " " not in name and len(name) > 3)
    )
    if looks_like_slug:
        return name.replace("_", " ").replace("-", " ").title()
    return name


def is_likely_job_title(name: str) -> bool:
    """Heuristic: filter role titles the model sometimes returns as skills."""
    lowered = (name or "").strip().lower()
    if not lowered:
        return True
    return any(lowered.endswith(suffix) for suffix in _JOB_TITLE_SUFFIXES)


def normalize_skill_label(name: str) -> str | None:
    """Clean a skill label for roadmap/gap use; return None if invalid."""
    label = humanize_skill_name((name or "").strip())
    if not label or len(label) < 2:
        return None
    if is_likely_job_title(label):
        return None
    return label[:150]


def ensure_skill(name: str, slug: str | None = None) -> Skill | None:
    """Get or create a Skill row; handle slug/name uniqueness races."""
    name = (name or "").strip()
    if not name:
        return None
    display_name = normalize_skill_label(name) or humanize_skill_name(name)
    if not display_name:
        return None
    s = (slug or slugify(display_name))[:50]
    if not s:
        return None

    try:
        skill, _ = Skill.objects.get_or_create(
            slug=s,
            defaults={"name": display_name, "category": "technical"},
        )
        return skill
    except IntegrityError:
        existing = Skill.objects.filter(slug=s).first()
        if existing:
            return existing
        existing = Skill.objects.filter(name__iexact=display_name).first()
        if existing:
            return existing
        raise
