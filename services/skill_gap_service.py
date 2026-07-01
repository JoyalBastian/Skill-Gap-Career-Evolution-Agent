"""Skill gap analysis via configured analysis provider (Gemini or Ollama).

Compares the user's skills to target career requirements. Falls back to
profile-derived skills when the LLM response is invalid or truncated.
"""
from __future__ import annotations

import logging
import re

from django.utils.text import slugify

from ai_engine.llm_client import LLMUnavailable, analysis_provider, chat_json
from apps.analytics.models import AIInsight
from apps.careers.models import CareerDomain, SkillGapReport
from apps.skills.models import UserSkill
from apps.users.models import Profile
from services.dto import SkillGapDTO
from services.skill_level_utils import compute_gap_levels, enrich_gap_context
from services.skill_utils import ensure_skill
from services.user_understanding_service import UserUnderstandingService

logger = logging.getLogger(__name__)

_CAREER_SKILL_DEFAULTS: list[tuple[tuple[str, ...], list[str]]] = [
    (
        ("data", "analyst", "science", "analytics"),
        ["SQL", "Python", "Statistics", "Data Visualization", "Excel"],
    ),
    (
        ("software", "developer", "engineer", "program", "full stack", "backend", "frontend"),
        ["Programming Fundamentals", "Git", "APIs", "Testing", "Algorithms"],
    ),
    (
        ("cloud", "devops", "infrastructure", "sre"),
        ["Linux", "Docker", "CI/CD", "Cloud Basics", "Networking"],
    ),
    (
        ("product", "manager", "pm"),
        ["Product Strategy", "User Research", "Roadmapping", "Stakeholder Communication"],
    ),
    (
        ("design", "ux", "ui"),
        ["User Research", "Wireframing", "Figma", "Visual Design", "Prototyping"],
    ),
    (
        ("marketing", "sales", "growth"),
        ["Digital Marketing", "Analytics", "Content Strategy", "CRM", "Communication"],
    ),
    (
        ("security", "cyber"),
        ["Networking", "Linux", "Security Fundamentals", "Risk Assessment", "Scripting"],
    ),
    (
        ("ai", "machine learning", "ml", "artificial intelligence"),
        ["Python", "Statistics", "Machine Learning Basics", "Data Wrangling", "Model Evaluation"],
    ),
]


def _clip_int(value, lo: int = 1, hi: int = 5) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        v = 3
    return max(lo, min(hi, v))


def _is_truncated_json_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "valid json" in msg or "truncated" in msg


def default_skills_for_career(career: CareerDomain, profile: Profile | None = None) -> list[str]:
    """Derive a skill list from career name and profile when the LLM is unavailable."""
    name_lower = (career.name or "").lower()
    skills: list[str] = []

    for keywords, defaults in _CAREER_SKILL_DEFAULTS:
        if any(keyword in name_lower for keyword in keywords):
            skills.extend(defaults)
            break

    if not skills:
        if career.is_technical:
            skills = [
                "Core Technical Skills",
                "Problem Solving",
                "Documentation",
                "Collaboration",
                "Project Practice",
            ]
        else:
            skills = [
                "Domain Knowledge",
                "Communication",
                "Analysis",
                "Planning",
                "Professional Skills",
            ]

    if profile:
        rc = profile.resume_context or {}
        prep = rc.get("interview_prep") or {}
        for part in re.split(r"[,;/|]+", prep.get("focus_areas") or ""):
            part = part.strip()
            if len(part) > 2:
                skills.insert(0, part.title())
        persona = rc.get("persona") or {}
        for interest in (persona.get("interests") or [])[:2]:
            interest = str(interest).strip()
            if interest:
                skills.insert(0, interest.title())
        for skill in (rc.get("skills") or [])[:3]:
            skill = str(skill).strip()
            if skill:
                skills.append(skill.title())

    seen: set[str] = set()
    unique: list[str] = []
    for skill in skills:
        key = skill.lower()
        if key not in seen:
            seen.add(key)
            unique.append(skill)
    return unique[:8]


def _collect_stated_skills(profile: Profile | None) -> tuple[list[dict], str]:
    user_skill_list: list[dict] = []
    stated_skills_section = ""
    if profile:
        user_skills_qs = UserSkill.objects.filter(user_id=profile.user_id).select_related("skill")
        user_skill_list = [
            {"name": us.skill.name, "proficiency": us.proficiency}
            for us in user_skills_qs
        ]

    if not user_skill_list and profile:
        rc = profile.resume_context or {}
        persona = rc.get("persona") or {}
        stated: list[str] = []
        if rc.get("skills"):
            stated.extend(str(s) for s in rc["skills"][:15])
        if persona.get("interests"):
            stated.extend(str(i) for i in persona["interests"][:8])
        prep = rc.get("interview_prep") or {}
        if prep.get("focus_areas"):
            stated.append(str(prep["focus_areas"]))
        if prep.get("current_title"):
            stated.append(f"Current focus: {prep['current_title']}")
        stated = [s.strip() for s in stated if s and str(s).strip()]
        if stated:
            stated_skills_section = (
                "STATED SKILLS / INTERESTS (from interview — treat as partial evidence, proficiency unknown):\n"
                + "\n".join(f"- {s}" for s in stated[:20])
                + "\n\n"
            )
    return user_skill_list, stated_skills_section


class SkillGapService:
    def _skill_count_rule(self) -> str:
        if analysis_provider() == "ollama":
            return "4. Return exactly 5-6 skills in required_skills.\n"
        return "4. Return 8-15 skills in required_skills.\n"

    def _ollama_max_tokens(self, compact: bool) -> int:
        return 3072 if compact else 4096

    def _build_prompt(
        self,
        career: CareerDomain,
        profile_text: str,
        user_skill_list: list[dict],
        stated_skills_section: str,
    ) -> str:
        is_ollama = analysis_provider() == "ollama"
        explanation_rule = (
            "6. explanation max 60 characters.\n"
            if is_ollama
            else "6. explanation: one sentence grounded in this user's profile.\n"
        )
        return (
            f"You are a career advisor. Compare the user's existing skills to the requirements "
            f"of becoming a {career.name}.\n\n"
            "STRICT RULES:\n"
            "1. Base analysis ONLY on the user profile and skills listed below.\n"
            "2. importance and required_proficiency MUST be integers from 1 to 5.\n"
            "3. Set is_missing=true ONLY when user proficiency is below required_proficiency or skill is absent.\n"
            f"{self._skill_count_rule()}"
            "5. Use consistent skill names (no duplicates).\n"
            f"{explanation_rule}\n"
            f"USER PROFILE:\n{profile_text}\n\n"
            f"{stated_skills_section}"
            f"USER SKILLS (proficiency 1-5):\n"
            + (
                "\n".join(f"- {s['name']} ({s['proficiency']}/5)" for s in user_skill_list)
                or "(none yet — use profile and stated interests)"
            )
            + f"\n\nTARGET CAREER: {career.name}\n"
            f"CAREER DESCRIPTION: {career.description}\n\n"
            "Respond ONLY with a JSON object:\n"
            "{\n"
            '  "employability_score": number 0-100,\n'
            '  "required_skills": [\n'
            "    {\n"
            '      "name": "Skill name",\n'
            '      "slug": "lowercase-hyphen",\n'
            '      "importance": 1-5,\n'
            '      "required_proficiency": 1-5,\n'
            '      "estimated_user_proficiency": 1-5,\n'
            '      "is_missing": true/false,\n'
            '      "explanation": "short reason"\n'
            "    }\n"
            "  ]\n"
            "}"
        )

    def _fetch_gap_data(
        self,
        career: CareerDomain,
        profile_text: str,
        user_skill_list: list[dict],
        stated_skills_section: str,
    ) -> dict:
        compact_attempts = [False, True] if analysis_provider() == "ollama" else [False]
        last_exc: BaseException | None = None

        for compact in compact_attempts:
            prompt = self._build_prompt(career, profile_text, user_skill_list, stated_skills_section)
            if compact:
                prompt = prompt.replace(
                    "4. Return exactly 5-6 skills in required_skills.\n",
                    "4. Return exactly 4 skills in required_skills.\n",
                )
            max_tokens = (
                self._ollama_max_tokens(compact)
                if analysis_provider() == "ollama"
                else 3072
            )
            try:
                data = chat_json(
                    prompt,
                    max_output_tokens=max_tokens,
                    provider=analysis_provider(),
                )
                if isinstance(data, dict) and data.get("required_skills"):
                    return data
                raise LLMUnavailable(
                    "Skill gap analysis did not return required_skills.",
                    provider=analysis_provider(),
                )
            except LLMUnavailable as exc:
                last_exc = exc
                if not _is_truncated_json_error(exc) or compact:
                    raise
                logger.warning(
                    "Skill gap JSON truncated for career=%s; retrying with fewer skills.",
                    career.name,
                )

        raise last_exc or LLMUnavailable(
            "Skill gap analysis failed.",
            provider=analysis_provider(),
        )

    def _parse_gap_items(
        self,
        required: list,
        user_skills_qs,
    ) -> tuple[list[SkillGapDTO], list[dict]]:
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
            if user_prof == 0:
                est = item.get("estimated_user_proficiency")
                if est is not None:
                    user_prof = _clip_int(est, lo=0, hi=5)
            is_missing = bool(item.get("is_missing", user_prof < req_prof))
            if user_prof >= req_prof and user_prof > 0:
                is_missing = False

            explanation = (item.get("explanation") or "")[:200]
            start_level, next_lvl = compute_gap_levels(user_prof, req_prof)

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
                    "user_proficiency": user_prof,
                    "required_proficiency": req_prof,
                    "recommended_start_level": start_level,
                    "next_level": next_lvl,
                    "explanation": explanation,
                    "gap_score": dto.gap_score,
                })

        return gaps, missing

    def _save_gap_report(
        self,
        user_id: int,
        career: CareerDomain,
        gaps: list[SkillGapDTO],
        missing: list[dict],
        employability: float,
        *,
        source: str = "llm",
    ) -> dict:
        min_gaps = 2 if analysis_provider() == "ollama" else 3
        if len(gaps) < min_gaps:
            raise LLMUnavailable(
                "Skill gap analysis returned too few valid skills.",
                provider=analysis_provider(),
            )

        missing.sort(key=lambda g: g.get("importance", 0), reverse=True)
        prioritized = [
            {
                "skill": m["skill_name"],
                "slug": m["slug"],
                "gap_score": m.get("gap_score", 0),
                "user_proficiency": m.get("user_proficiency", 0),
                "required_proficiency": m.get("required_proficiency", 3),
                "recommended_start_level": m.get("recommended_start_level", "beginner"),
                "next_level": m.get("next_level"),
            }
            for m in missing[:10]
        ]

        SkillGapReport.objects.filter(user_id=user_id, career=career).delete()
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
                "source": source,
            },
        )

        return {
            "report_id": report.id,
            "employability_score": report.employability_score,
            "gaps": gaps,
            "prioritized": prioritized,
        }

    def build_local_gap_fallback(self, user_id: int, career_id: int) -> dict:
        career = CareerDomain.objects.get(id=career_id)
        profile = Profile.objects.filter(user_id=user_id).first()
        user_skills_qs = UserSkill.objects.filter(user_id=user_id).select_related("skill")
        user_prof_lookup = {us.skill.name.lower(): us.proficiency for us in user_skills_qs}

        skill_names = default_skills_for_career(career, profile)
        gaps: list[SkillGapDTO] = []
        missing: list[dict] = []

        for index, name in enumerate(skill_names):
            slug = slugify(name)[:50]
            skill = ensure_skill(name, slug)
            if not skill:
                continue
            user_prof = user_prof_lookup.get(name.lower(), 1)
            req_prof = 4
            importance = max(3, 5 - index // 2)
            is_missing = user_prof < req_prof
            start_level, next_lvl = compute_gap_levels(user_prof, req_prof)
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
                    "user_proficiency": user_prof,
                    "required_proficiency": req_prof,
                    "recommended_start_level": start_level,
                    "next_level": next_lvl,
                    "explanation": f"Important for {career.name} based on your profile.",
                    "gap_score": dto.gap_score,
                })

        if not gaps:
            raise LLMUnavailable(
                "Local skill gap fallback produced no skills.",
                provider="local",
            )

        employability = round(max(25.0, 70.0 - len(missing) * 6), 1)
        logger.info("Using local skill gap fallback for user=%s career=%s", user_id, career.name)
        return self._save_gap_report(
            user_id,
            career,
            gaps,
            missing,
            employability,
            source="local_fallback",
        )

    def analyze_gaps(self, user_id: int, career_id: int) -> dict:
        career = CareerDomain.objects.get(id=career_id)
        try:
            return self._analyze_gaps_llm(user_id, career)
        except LLMUnavailable as exc:
            logger.warning(
                "Skill gap LLM failed for user=%s career=%s: %s",
                user_id,
                career.name,
                exc,
            )
            return self.build_local_gap_fallback(user_id, career_id)

    def _analyze_gaps_llm(self, user_id: int, career: CareerDomain) -> dict:
        uus = UserUnderstandingService()
        profile_text = uus.get_user_profile_text(user_id)
        profile = Profile.objects.filter(user_id=user_id).first()
        user_skill_list, stated_skills_section = _collect_stated_skills(profile)
        user_skills_qs = UserSkill.objects.filter(user_id=user_id).select_related("skill")

        data = self._fetch_gap_data(career, profile_text, user_skill_list, stated_skills_section)
        employability = float(data.get("employability_score") or 0)
        employability = max(0, min(100, employability))
        required = data.get("required_skills") or []

        gaps, missing = self._parse_gap_items(required, user_skills_qs)
        if not gaps:
            raise LLMUnavailable(
                "Skill gap analysis returned no valid skills.",
                provider=analysis_provider(),
            )

        return self._save_gap_report(user_id, career, gaps, missing, employability)

    def get_latest_report(self, user_id: int):
        return SkillGapReport.objects.filter(user_id=user_id).first()


def find_gap_info_for_skill(gap_report, skill) -> dict | None:
    """Return enriched gap context for a skill in the latest report, or None."""
    if not gap_report:
        return None

    slug = skill.slug
    name_lower = skill.name.lower()

    for item in gap_report.missing_skills or []:
        if item.get("slug") == slug or (
            (item.get("skill_name") or "").lower() == name_lower
        ):
            return enrich_gap_context(item)

    for item in gap_report.prioritized_skills or []:
        if item.get("slug") == slug:
            return enrich_gap_context(item)

    return None
