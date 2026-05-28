"""Gemini-only skill gap analysis.

No CareerSkillRequirement table, no embedding similarity. Gemini is given the
user's known skills and the target career and returns the missing skills with
importance scores and explanations.
"""
from __future__ import annotations

import logging

from django.utils.text import slugify

from ai_engine.llm_client import LLMUnavailable, chat_json
from apps.analytics.models import AIInsight
from apps.careers.models import CareerDomain, SkillGapReport
from apps.skills.models import UserSkill
from services.dto import SkillGapDTO
from services.skill_utils import ensure_skill
from services.user_understanding_service import UserUnderstandingService

logger = logging.getLogger(__name__)


def _clip_int(value, lo: int = 1, hi: int = 5) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        v = 3
    return max(lo, min(hi, v))


class SkillGapService:
    def analyze_gaps(self, user_id: int, career_id: int) -> dict:
        career = CareerDomain.objects.get(id=career_id)
        uus = UserUnderstandingService()
        profile_text = uus.get_user_profile_text(user_id)

        user_skills_qs = UserSkill.objects.filter(user_id=user_id).select_related("skill")
        user_skill_list = [
            {"name": us.skill.name, "proficiency": us.proficiency}
            for us in user_skills_qs
        ]

        prompt = (
            f"You are a career advisor. Compare the user's existing skills to the requirements "
            f"of becoming a {career.name}.\n\n"
            "STRICT RULES:\n"
            "1. Base analysis ONLY on the user profile and skills listed below.\n"
            "2. importance and required_proficiency MUST be integers from 1 to 5.\n"
            "3. Set is_missing=true ONLY when user proficiency is below required_proficiency or skill is absent.\n"
            "4. Return 8-15 skills in required_skills.\n"
            "5. Use consistent skill names (no duplicates).\n\n"
            f"USER PROFILE:\n{profile_text}\n\n"
            f"USER SKILLS (proficiency 1-5):\n"
            + ("\n".join(f"- {s['name']} ({s['proficiency']}/5)" for s in user_skill_list) or "(none yet)")
            + f"\n\nTARGET CAREER: {career.name}\n"
            f"CAREER DESCRIPTION: {career.description}\n\n"
            "Respond ONLY with a JSON object:\n"
            "{\n"
            "  \"employability_score\": number 0-100,\n"
            "  \"required_skills\": [\n"
            "    {\n"
            "      \"name\": \"Skill name\",\n"
            "      \"slug\": \"lowercase-hyphen\",\n"
            "      \"importance\": 1-5,\n"
            "      \"required_proficiency\": 1-5,\n"
            "      \"is_missing\": true/false,\n"
            "      \"explanation\": \"1 sentence grounded in this user's profile\"\n"
            "    }\n"
            "  ]\n"
            "}"
        )

        data = chat_json(prompt)
        if not isinstance(data, dict):
            raise LLMUnavailable("Skill gap analysis did not return a valid JSON object.", provider="unknown")

        employability = float(data.get("employability_score") or 0)
        employability = max(0, min(100, employability))
        required = data.get("required_skills") or []

        gaps: list[SkillGapDTO] = []
        missing: list[dict] = []
        user_prof_lookup = {us.skill.name.lower(): us.proficiency for us in user_skills_qs}
        seen_slugs: set[str] = set()

        for item in required:
            if not isinstance(item, dict):
                continue
            name = (item.get("name") or "").strip()
            if not name:
                continue
            slug = (item.get("slug") or slugify(name))[:50]
            if slug in seen_slugs:
                continue
            seen_slugs.add(slug)

            skill = ensure_skill(name, slug)
            if not skill:
                continue

            importance = _clip_int(item.get("importance"))
            req_prof = _clip_int(item.get("required_proficiency"))
            user_prof = user_prof_lookup.get(name.lower(), 0)
            is_missing = bool(item.get("is_missing", user_prof < req_prof))
            if user_prof >= req_prof and user_prof > 0:
                is_missing = False

            explanation = item.get("explanation") or ""

            dto = SkillGapDTO(
                skill_name=skill.name,
                skill_slug=skill.slug,
                importance=importance,
                user_proficiency=user_prof,
                required_proficiency=req_prof,
                gap_score=float(max(0, req_prof - user_prof) + (importance if is_missing else 0)),
                is_missing=is_missing,
            )
            gaps.append(dto)
            if is_missing:
                missing.append({
                    "skill_name": skill.name,
                    "slug": skill.slug,
                    "importance": importance,
                    "explanation": explanation,
                    "gap_score": dto.gap_score,
                })

        if not gaps:
            raise LLMUnavailable("Skill gap analysis returned no valid skills.", provider="unknown")

        missing.sort(key=lambda g: g.get("importance", 0), reverse=True)
        prioritized = [
            {"skill": m["skill_name"], "slug": m["slug"], "gap_score": m.get("gap_score", 0)}
            for m in missing[:10]
        ]

        report = SkillGapReport.objects.create(
            user_id=user_id,
            career=career,
            employability_score=round(employability, 1),
            missing_skills=missing,
            prioritized_skills=prioritized,
        )

        AIInsight.objects.create(
            user_id=user_id,
            insight_type="skill_gap",
            payload={
                "employability_score": report.employability_score,
                "missing_count": len(missing),
                "career": career.name,
            },
        )

        return {
            "report_id": report.id,
            "employability_score": report.employability_score,
            "gaps": gaps,
            "prioritized": prioritized,
        }

    def get_latest_report(self, user_id: int):
        return SkillGapReport.objects.filter(user_id=user_id).first()
