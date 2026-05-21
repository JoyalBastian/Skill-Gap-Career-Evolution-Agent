"""Shared skill row helpers for AI pipeline services."""
from __future__ import annotations

from django.db import IntegrityError
from django.utils.text import slugify

from apps.skills.models import Skill


def ensure_skill(name: str, slug: str | None = None) -> Skill | None:
    """Get or create a Skill row; handle slug/name uniqueness races."""
    name = (name or "").strip()
    if not name:
        return None
    s = (slug or slugify(name))[:50]
    if not s:
        return None
    display_name = name[:150]

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
