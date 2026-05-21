"""Resume upload + Gemini-only structured extraction.

No SBERT, no spaCy. Skills mentioned by Gemini are created as Skill rows on
the fly so the rest of the pipeline (gaps, roadmap, recommendations) can
reference stable IDs.
"""
from __future__ import annotations

import logging

from ai_engine.llm_client import GeminiUnavailable, chat_json
from ai_engine.resume_analysis.pdf_parser import extract_text_from_pdf
from apps.skills.models import ResumeAnalysisResult, Skill, UserSkill
from apps.users.models import Profile, ResumeUpload
from services.dto import ResumeAnalysisDTO
from services.skill_utils import ensure_skill

logger = logging.getLogger(__name__)

_SCHEMA_PROMPT = """Extract structured information from this resume text.

Resume:
{text}

Respond ONLY with valid JSON in this exact format:
{{
  "full_name": "string or empty",
  "email": "string or empty",
  "phone": "string or empty",
  "location": "string or empty",
  "current_title": "string or empty",
  "experience_years": number,
  "experience_level": "beginner|intermediate|advanced",
  "education": [{{"degree": "string", "field": "string", "institution": "string"}}],
  "job_titles": ["list of past job titles"],
  "skills": ["list of all skills, technologies, tools, frameworks mentioned"],
  "certifications": ["list of certifications"],
  "career_domain": "best matching career domain in lowercase-hyphen form (e.g. software-development, data-science, ui-ux-design)",
  "summary": "2-3 sentence professional summary",
  "employability_score": number from 0 to 100,
  "projects": ["short list of notable project names/descriptions"]
}}
"""


def _extract_with_gemini(text: str) -> dict:
    """Use Gemini to extract structured profile data from resume text."""
    prompt = _SCHEMA_PROMPT.format(text=text[:6000])
    data = chat_json(prompt)
    if not isinstance(data, dict):
        raise GeminiUnavailable("Resume extraction did not return a JSON object.")
    return data


class ResumeAnalysisService:
    def process_resume(self, resume_id: int) -> ResumeAnalysisDTO:
        resume = ResumeUpload.objects.get(id=resume_id)
        resume.status = "processing"
        resume.save(update_fields=["status"])

        try:
            text = extract_text_from_pdf(resume.file.path)
            resume.parsed_text = text
            resume.save(update_fields=["parsed_text"])

            if not text or not text.strip():
                raise GeminiUnavailable("No text could be extracted from the PDF.")

            data = _extract_with_gemini(text)

            skills_list = data.get("skills") or []
            skills_detected = []
            for skill_name in skills_list:
                if not isinstance(skill_name, str):
                    continue
                skill = ensure_skill(skill_name)
                if not skill:
                    continue
                skills_detected.append({
                    "name": skill.name,
                    "slug": skill.slug,
                    "confidence": 0.8,
                    "proficiency": 3,
                })
                UserSkill.objects.update_or_create(
                    user=resume.user,
                    skill=skill,
                    defaults={
                        "proficiency": 3,
                        "source": "resume",
                        "confidence": 0.8,
                    },
                )

            experience_years = float(data.get("experience_years") or 0)
            education = data.get("education") or []
            employability_score = float(data.get("employability_score") or min(100, len(skills_detected) * 8))
            summary = data.get("summary") or ""
            projects = data.get("projects") or []

            ResumeAnalysisResult.objects.update_or_create(
                resume=resume,
                defaults={
                    "skills_detected": skills_detected,
                    "experience_years": experience_years,
                    "education": education,
                    "projects": projects,
                    "employability_score": employability_score,
                    "summary": summary,
                },
            )

            # Save Gemini context into profile so the questionnaire/chatbot can use it
            profile, _ = Profile.objects.get_or_create(user=resume.user)
            profile.resume_context = {
                "full_name": data.get("full_name", ""),
                "email": data.get("email", ""),
                "phone": data.get("phone", ""),
                "location": data.get("location", ""),
                "current_title": data.get("current_title", ""),
                "experience_years": experience_years,
                "experience_level": data.get("experience_level", "beginner"),
                "education": education,
                "job_titles": data.get("job_titles", []) or [],
                "skills": [s["name"] for s in skills_detected[:20]],
                "certifications": data.get("certifications", []) or [],
                "career_domain": data.get("career_domain", ""),
                "summary": summary,
            }
            detected_level = data.get("experience_level", "")
            if detected_level in {"beginner", "intermediate", "advanced"}:
                profile.target_career_level = detected_level
            profile.save(update_fields=["resume_context", "target_career_level"])

            # Optional downstream pipeline (off by default — saves free-tier quota)
            from django.conf import settings

            if getattr(settings, "RUN_AI_PIPELINE_ON_RESUME", False):
                self._run_pipeline(resume.user)

            resume.status = "completed"
            resume.save(update_fields=["status"])

            return ResumeAnalysisDTO(
                skills_detected=skills_detected,
                experience_years=experience_years,
                education=education,
                employability_score=employability_score,
                summary=summary,
            )

        except Exception as e:
            resume.status = "failed"
            resume.save(update_fields=["status"])
            logger.error("Resume analysis failed: %s", e)
            raise

    def _run_pipeline(self, user):
        from services.career_prediction_service import CareerPredictionService
        from services.recommendation_service import RecommendationService
        from services.roadmap_service import RoadmapService
        from services.skill_gap_service import SkillGapService

        try:
            CareerPredictionService().run_prediction(user.id)
        except GeminiUnavailable as e:
            logger.warning("Career prediction skipped: %s", e)
            return

        prediction = user.career_predictions.order_by("rank").first()
        if prediction:
            try:
                SkillGapService().analyze_gaps(user.id, prediction.career_id)
            except GeminiUnavailable as e:
                logger.warning("Skill gap skipped: %s", e)

        try:
            RecommendationService().generate_recommendations(user.id)
        except GeminiUnavailable as e:
            logger.warning("Recommendations skipped: %s", e)

        try:
            RoadmapService().generate_roadmap(user.id)
        except GeminiUnavailable as e:
            logger.warning("Roadmap skipped: %s", e)
