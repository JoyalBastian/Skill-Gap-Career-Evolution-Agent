"""Gemini-only learning roadmap generator.

No JSON templates, no fallback default steps. Gemini produces the full
roadmap given the user profile and target career.
"""
from __future__ import annotations

import logging

from ai_engine.llm_client import GeminiUnavailable, chat_json
from apps.analytics.models import AIInsight
from apps.careers.models import CareerDomain, SkillGapReport
from apps.roadmap.models import Roadmap, RoadmapStep
from apps.skills.models import Skill
from apps.users.models import Profile
from services.skill_utils import ensure_skill
from services.user_understanding_service import UserUnderstandingService

logger = logging.getLogger(__name__)


def _clip_weeks(value) -> int:
    try:
        w = int(value)
    except (TypeError, ValueError):
        w = 2
    return max(1, min(12, w))


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
            prediction = (
                profile.user.career_predictions.order_by("rank").first()
                if profile else None
            )
            career = prediction.career if prediction else None
        if not career:
            logger.info("No career available for user %s; skipping roadmap.", user_id)
            return None

        gap_report = SkillGapReport.objects.filter(user_id=user_id, career=career).first()
        gap_slugs: list[str] = []
        gap_detail_lines: list[str] = []
        if gap_report:
            for s in gap_report.prioritized_skills or []:
                slg = s.get("slug")
                nm = s.get("skill") or s.get("skill_name")
                if slg:
                    gap_slugs.append(slg)
                if nm:
                    gap_detail_lines.append(f"- {nm} (priority gap)")

        top_gaps = gap_detail_lines[:3]
        profile_text = UserUnderstandingService().get_user_profile_text(user_id)

        gap_section = ""
        if top_gaps:
            gap_section = (
                "TOP PRIORITY SKILL GAPS (each MUST have at least one dedicated roadmap step):\n"
                + "\n".join(top_gaps)
                + "\n\n"
            )

        prompt = (
            "You are a career education expert. Create a personalized learning roadmap.\n\n"
            "STRICT RULES:\n"
            "1. Generate 5-8 sequential learning steps ordered from foundational to advanced.\n"
            "2. Each step must include 2-5 skills in the skills array.\n"
            "3. estimated_weeks MUST be an integer from 1 to 12.\n"
            "4. prerequisites must reference title strings from earlier steps in this roadmap.\n"
            "5. Steps must be specific to the user profile — avoid generic filler.\n"
            + ("6. Include at least one step that explicitly addresses each top priority gap listed below.\n" if top_gaps else "")
            + "\n"
            f"TARGET CAREER: {career.name}\n"
            f"CAREER DESCRIPTION: {career.description}\n"
            f"LEVEL: {level}\n\n"
            f"USER PROFILE:\n{profile_text}\n\n"
            f"{gap_section}"
            "Respond ONLY with a JSON object:\n"
            "{\n"
            "  \"steps\": [\n"
            "    {\n"
            "      \"title\": \"Step title\",\n"
            "      \"description\": \"What to learn and why it matters for this user\",\n"
            "      \"estimated_weeks\": number,\n"
            "      \"skills\": [\"skill name 1\", \"skill name 2\"],\n"
            "      \"prerequisites\": [\"earlier step title\"]\n"
            "    }\n"
            "  ]\n"
            "}"
        )

        data = chat_json(prompt)
        steps_data = []
        if isinstance(data, dict):
            steps_data = data.get("steps") or []
        elif isinstance(data, list):
            steps_data = data
        if not steps_data:
            raise GeminiUnavailable("Gemini returned no roadmap steps.")

        validated_steps = []
        for step_data in steps_data:
            if not isinstance(step_data, dict):
                continue
            title = (step_data.get("title") or "").strip()
            if not title:
                continue
            skills = [
                str(s).strip() for s in (step_data.get("skills") or [])
                if isinstance(s, str) and str(s).strip()
            ][:5]
            if len(skills) < 2 and skills:
                skills = skills
            validated_steps.append({
                "title": title[:255],
                "description": (step_data.get("description") or "")[:2000],
                "estimated_weeks": _clip_weeks(step_data.get("estimated_weeks")),
                "skills": skills if skills else [],
                "prerequisites": [
                    str(p).strip()
                    for p in (step_data.get("prerequisites") or [])
                    if str(p).strip()
                ],
            })

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
            order += 1

        covered_skills = set()
        for rs in roadmap.steps.prefetch_related("skills"):
            for s in rs.skills.all():
                covered_skills.add(s.slug)
        for gap_slug in gap_slugs[:3]:
            if gap_slug in covered_skills:
                continue
            skill = Skill.objects.filter(slug=gap_slug).first()
            if not skill:
                continue
            step = RoadmapStep.objects.create(
                roadmap=roadmap,
                order=order,
                title=f"Bridge: {skill.name}",
                description=f"Focus on developing {skill.name} to close your skill gap.",
                estimated_weeks=3,
            )
            step.skills.add(skill)
            order += 1

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
