"""Gemini-only learning roadmap generator.

No JSON templates, no fallback default steps. Gemini produces the full
roadmap given the user profile and target career.
"""
from __future__ import annotations

import logging
import re

from ai_engine.llm_client import GeminiUnavailable, active_provider, chat_json
from apps.analytics.models import AIInsight
from apps.careers.models import CareerDomain, CareerPrediction, SkillGapReport
from apps.roadmap.models import Roadmap, RoadmapStep
from apps.skills.models import Skill
from apps.users.models import Profile
from services.skill_utils import ensure_skill, humanize_skill_name, normalize_skill_label
from services.user_understanding_service import UserUnderstandingService

logger = logging.getLogger(__name__)

_VAGUE_STEP_RE = re.compile(
    r"\b(financial growth|continuous learning|problem.?solving opportunities|career success)\b",
    re.I,
)

_LEVEL_GUIDANCE = {
    "beginner": (
        "Beginner level: start with foundational concepts and one core tool/language. "
        "Step 1 must be entry-level (e.g. Python basics, math/stats fundamentals). "
        "Do NOT start with cloud platforms, DevOps, SDLC, or deployment. "
        "Order steps from easiest to hardest; estimated_weeks should generally increase."
    ),
    "intermediate": (
        "Intermediate level: assume basic programming knowledge. "
        "Focus on frameworks, projects, and applied skills before architecture or leadership topics."
    ),
    "advanced": (
        "Advanced level: emphasize system design, optimization, MLOps/production, and specialization."
    ),
}


def _clip_weeks(value) -> int:
    try:
        w = int(value)
    except (TypeError, ValueError):
        w = 2
    return max(1, min(12, w))


def _sanitize_skills(raw_skills: list) -> list[str]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for item in raw_skills or []:
        if not isinstance(item, str):
            continue
        label = normalize_skill_label(item)
        if not label:
            continue
        key = label.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(label)
        if len(cleaned) >= 5:
            break
    return cleaned


def _is_vague_step(title: str, description: str) -> bool:
    text = f"{title} {description}".strip()
    return bool(_VAGUE_STEP_RE.search(text))


def _gap_display_names(gap_report: SkillGapReport | None) -> dict[str, str]:
    """Map gap slugs to human-readable skill names from the gap report."""
    names: dict[str, str] = {}
    if not gap_report:
        return names
    for source in (gap_report.prioritized_skills or [], gap_report.missing_skills or []):
        for item in source:
            if not isinstance(item, dict):
                continue
            slug = (item.get("slug") or "").strip()
            if not slug:
                continue
            raw = (item.get("skill") or item.get("skill_name") or "").strip()
            names[slug] = humanize_skill_name(raw) if raw else humanize_skill_name(slug)
    return names


def _order_steps(steps: list[dict], level: str) -> list[dict]:
    if level == "beginner":
        return sorted(steps, key=lambda s: (s["estimated_weeks"], s["title"].lower()))
    return steps


def _build_gap_section(gap_detail_lines: list[str]) -> str:
    if not gap_detail_lines:
        return ""
    return (
        "TOP PRIORITY SKILL GAPS — weave each into a dedicated roadmap step "
        "(use the exact skill name in the step title or skills array; do NOT append a separate "
        "'Bridge' step):\n"
        + "\n".join(gap_detail_lines)
        + "\n\n"
    )


class RoadmapService:
    def generate_roadmap(
        self,
        user_id: int,
        career_id: int | None = None,
        level: str | None = None,
    ) -> Roadmap | None:
        profile = Profile.objects.filter(user_id=user_id).first()
        level = level or (profile.target_career_level if profile else "beginner")

        career = None
        if career_id:
            career = CareerDomain.objects.filter(id=career_id).first()
        if not career and profile:
            career = profile.target_career
        if not career:
            prediction = CareerPrediction.objects.filter(user_id=user_id).order_by("rank").first()
            career = prediction.career if prediction else None
        if not career:
            logger.info("No career available for user %s; skipping roadmap.", user_id)
            return None

        gap_report = SkillGapReport.objects.filter(user_id=user_id, career=career).first()
        gap_names_by_slug = _gap_display_names(gap_report)
        gap_slugs: list[str] = []
        gap_detail_lines: list[str] = []
        if gap_report:
            for s in gap_report.prioritized_skills or []:
                slg = (s.get("slug") or "").strip()
                nm = gap_names_by_slug.get(slg) or humanize_skill_name(
                    s.get("skill") or s.get("skill_name") or slg
                )
                if slg:
                    gap_slugs.append(slg)
                if nm:
                    gap_detail_lines.append(f"- {nm} (priority gap)")

        top_gaps = gap_detail_lines[:5]
        profile_text = UserUnderstandingService().get_user_profile_text(user_id)
        level_hint = _LEVEL_GUIDANCE.get(level, _LEVEL_GUIDANCE["beginner"])

        step_rule = (
            "1. Generate exactly 5 sequential learning steps ordered from foundational to advanced.\n"
            if active_provider() == "ollama"
            else "1. Generate 5-7 sequential learning steps ordered from foundational to advanced.\n"
        )

        prompt = (
            "You are a career education expert. Create a practical, role-specific learning roadmap.\n\n"
            "STRICT RULES:\n"
            + step_rule +
            "2. Each step must include 2-5 skills in the skills array.\n"
            "3. skills must be learnable topics, tools, or technologies — NEVER job titles "
            "(e.g. use 'TensorFlow' not 'Machine Learning Engineer').\n"
            "4. estimated_weeks MUST be an integer from 1 to 12 and should increase across steps.\n"
            "5. description max 150 characters; be concrete about what the learner will do.\n"
            "6. prerequisites must reference title strings from earlier steps in this roadmap.\n"
            "7. Every step must teach technical skills for the target career — no generic career advice, "
            "financial growth, or soft motivational filler.\n"
            f"8. {level_hint}\n"
            + (
                "9. Include at least one step that explicitly teaches each top priority gap listed below.\n"
                if top_gaps
                else ""
            )
            + "\n"
            f"TARGET CAREER: {career.name}\n"
            f"CAREER DESCRIPTION: {career.description}\n"
            f"LEVEL: {level}\n\n"
            f"USER PROFILE:\n{profile_text}\n\n"
            f"{_build_gap_section(top_gaps)}"
            "Respond ONLY with a JSON object:\n"
            "{\n"
            '  "steps": [\n'
            "    {\n"
            '      "title": "Step title",\n'
            '      "description": "What to learn and why it matters for this user",\n'
            '      "estimated_weeks": number,\n'
            '      "skills": ["skill name 1", "skill name 2"],\n'
            '      "prerequisites": ["earlier step title"]\n'
            "    }\n"
            "  ]\n"
            "}"
        )

        data = chat_json(
            prompt,
            max_output_tokens=4096 if active_provider() == "ollama" else None,
        )
        steps_data = []
        if isinstance(data, dict):
            steps_data = data.get("steps") or []
        elif isinstance(data, list):
            steps_data = data
        if not steps_data:
            raise GeminiUnavailable("Gemini returned no roadmap steps.")

        validated_steps: list[dict] = []
        for step_data in steps_data:
            if not isinstance(step_data, dict):
                continue
            title = (step_data.get("title") or "").strip()
            description = (step_data.get("description") or "").strip()
            if not title or _is_vague_step(title, description):
                continue
            skills = _sanitize_skills(step_data.get("skills") or [])
            validated_steps.append({
                "title": title[:255],
                "description": description[:2000],
                "estimated_weeks": _clip_weeks(step_data.get("estimated_weeks")),
                "skills": skills,
                "prerequisites": [
                    str(p).strip()
                    for p in (step_data.get("prerequisites") or [])
                    if str(p).strip()
                ],
            })

        validated_steps = _order_steps(validated_steps, level)

        if not validated_steps:
            raise GeminiUnavailable("No valid roadmap steps after validation.")

        Roadmap.objects.filter(user_id=user_id, is_active=True).update(is_active=False)
        roadmap = Roadmap.objects.create(
            user_id=user_id,
            target_career=career,
            level=level,
            title=f"{career.name} Learning Path ({level.title()})",
            description=f"Personalized roadmap for {career.name} at {level} level.",
            is_active=True,
        )

        order = 1
        covered_slugs: set[str] = set()
        for step_data in validated_steps:
            step = RoadmapStep.objects.create(
                roadmap=roadmap,
                order=order,
                title=step_data["title"],
                description=step_data["description"],
                estimated_weeks=step_data["estimated_weeks"],
                prerequisites=step_data["prerequisites"],
            )
            for s_name in step_data["skills"]:
                sk = ensure_skill(s_name)
                if sk:
                    step.skills.add(sk)
                    covered_slugs.add(sk.slug)
            order += 1

        for gap_slug in gap_slugs[:3]:
            if gap_slug in covered_slugs:
                continue
            display_name = gap_names_by_slug.get(gap_slug) or humanize_skill_name(gap_slug)
            skill = Skill.objects.filter(slug=gap_slug).first()
            if not skill:
                skill = ensure_skill(display_name, gap_slug)
            if not skill:
                continue
            step = RoadmapStep.objects.create(
                roadmap=roadmap,
                order=order,
                title=f"Build {display_name} Skills",
                description=(
                    f"Learn core {display_name.lower()} concepts and practice exercises "
                    f"to close your skill gap for {career.name}."
                )[:2000],
                estimated_weeks=3,
            )
            step.skills.add(skill)
            covered_slugs.add(skill.slug)
            order += 1

        for idx, step in enumerate(roadmap.steps.order_by("order"), start=1):
            if step.order != idx:
                step.order = idx
                step.save(update_fields=["order"])

        AIInsight.objects.create(
            user_id=user_id,
            insight_type="roadmap",
            payload={"roadmap_id": roadmap.id, "career": career.name, "level": level},
        )
        return roadmap

    def get_active_roadmap(self, user_id: int):
        return Roadmap.objects.filter(user_id=user_id, is_active=True).prefetch_related(
            "steps__skills"
        ).first()

    def get_roadmap_for_career(self, user_id: int, career_id: int) -> Roadmap | None:
        """Active roadmap for a career, or the most recent one for that target."""
        qs = Roadmap.objects.filter(user_id=user_id, target_career_id=career_id)
        active = qs.filter(is_active=True).order_by("-generated_at").first()
        if active:
            return active
        return qs.order_by("-generated_at").first()

    def get_steps_for_skill(self, user_id: int, skill_slug: str):
        """Roadmap steps from the active roadmap that target this skill."""
        roadmap = self.get_active_roadmap(user_id)
        if not roadmap:
            return [], None
        steps = list(
            roadmap.steps.filter(skills__slug=skill_slug)
            .prefetch_related("skills")
            .order_by("order")
            .distinct()
        )
        return steps, roadmap
