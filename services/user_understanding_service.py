"""Gemini-only user understanding.

No more SBERT embeddings, no spaCy preprocessing, no Interest/Trait catalogs.
We just aggregate the user's profile text and ask Gemini for a structured
persona (top traits, interests, motivations) that we save into
`Profile.resume_context['persona']` so the rest of the pipeline can read it.
"""
from __future__ import annotations

import logging

from ai_engine.llm_client import GeminiUnavailable, chat_json
from apps.questionnaire.models import AIAnswer, QuestionnaireSession
from apps.users.models import Profile

logger = logging.getLogger(__name__)

_MAX_RECENT_QA = 8


class UserUnderstandingService:
    """Build a natural-language understanding of the user via Gemini."""

    def get_user_profile_text(self, user_id: int) -> str:
        """Aggregate all available text for downstream Gemini prompts."""
        texts: list[str] = []

        prof = Profile.objects.filter(user_id=user_id).first()
        if prof:
            rc = prof.resume_context or {}
            if rc.get("summary"):
                texts.append(rc["summary"])
            if rc.get("current_title"):
                texts.append(f"Current role: {rc['current_title']}")
            if rc.get("skills"):
                texts.append("Skills: " + ", ".join(rc["skills"]))
            if rc.get("job_titles"):
                texts.append("Past roles: " + ", ".join(rc["job_titles"]))
            if rc.get("career_domain"):
                texts.append(f"Career domain: {rc['career_domain']}")
            for edu in rc.get("education", []) or []:
                parts = [edu.get("degree", ""), edu.get("field", ""), edu.get("institution", "")]
                joined = " ".join(p for p in parts if p)
                if joined:
                    texts.append(joined)
            persona = rc.get("persona") or {}
            if persona.get("summary"):
                texts.append("Persona: " + persona["summary"])
            if persona.get("traits"):
                texts.append("Traits: " + ", ".join(persona["traits"]))
            if persona.get("interests"):
                texts.append("Interests: " + ", ".join(persona["interests"]))
            if persona.get("motivations"):
                texts.append("Motivations: " + ", ".join(persona["motivations"]))
            if persona.get("work_style"):
                texts.append(f"Work style: {persona['work_style']}")
            if prof.bio:
                texts.append(prof.bio)

        recent_qa = self._get_recent_interview_qa(user_id, limit=_MAX_RECENT_QA)
        if recent_qa:
            texts.append("Recent interview answers:")
            texts.extend(recent_qa)

        joined = "\n".join(t for t in texts if t)
        return joined or "User has not yet provided profile data."

    def _get_recent_interview_qa(self, user_id: int, limit: int = 8) -> list[str]:
        session = (
            QuestionnaireSession.objects.filter(user_id=user_id, status="completed")
            .order_by("-completed_at", "-id")
            .first()
        )
        if not session:
            return []
        lines = []
        answers = (
            AIAnswer.objects.filter(session=session)
            .select_related("question")
            .order_by("question__order")
        )
        for answer in answers[:limit]:
            a = answer.get_answer_text()
            if a:
                lines.append(f"Q: {answer.question.text}\nA: {a}")
        return lines

    def build_user_persona(self, user_id: int) -> dict:
        """Ask Gemini to summarize the user as a persona JSON and save to Profile."""
        profile_text = self.get_user_profile_text(user_id)

        prompt = (
            "You are a career counselor. From the data below, produce a concise persona of the user.\n"
            "Base every field ONLY on facts present in the data — do not invent credentials or experience.\n\n"
            f"DATA:\n{profile_text}\n\n"
            "Respond ONLY with valid JSON:\n"
            "{\n"
            "  \"summary\": \"2-3 sentence portrait of who this user is professionally\",\n"
            "  \"traits\": [\"3-6 personality traits, e.g. analytical, collaborative\"],\n"
            "  \"interests\": [\"3-6 career interests, e.g. AI engineering, product design\"],\n"
            "  \"motivations\": [\"2-4 motivators, e.g. solving complex problems, financial growth\"],\n"
            "  \"work_style\": \"short description of how the user prefers to work\"\n"
            "}"
        )

        data = chat_json(prompt)
        if not isinstance(data, dict):
            raise GeminiUnavailable("Persona builder did not return a JSON object.")

        return self._save_persona(user_id, data)

    def build_local_persona_fallback(self, user_id: int, session_id: int | None = None) -> dict:
        """Build a minimal persona from interview answers when Gemini is unavailable."""
        prof = Profile.objects.filter(user_id=user_id).first()
        rc = (prof.resume_context or {}) if prof else {}

        answers_text: list[str] = []
        if session_id:
            session = QuestionnaireSession.objects.filter(id=session_id).first()
        else:
            session = (
                QuestionnaireSession.objects.filter(user_id=user_id, status="completed")
                .order_by("-completed_at")
                .first()
            )
        if session:
            for ans in (
                AIAnswer.objects.filter(session=session)
                .select_related("question")
                .order_by("question__order")[:_MAX_RECENT_QA]
            ):
                text = ans.get_answer_text()
                if text:
                    answers_text.append(text)

        title = rc.get("current_title") or "professional"
        skills = rc.get("skills") or []
        summary_parts = [f"A {title} seeking career growth."]
        if answers_text:
            summary_parts.append(f"Key input from interview: {'; '.join(answers_text[:3])[:400]}")
        if rc.get("career_domain"):
            summary_parts.append(f"Domain interest: {rc['career_domain']}.")

        interests = list(rc.get("career_domain", "") and [rc["career_domain"]] or [])
        if skills:
            interests.extend(skills[:3])

        data = {
            "summary": " ".join(summary_parts)[:500],
            "traits": ["motivated", "adaptable"],
            "interests": interests[:6] or ["career development"],
            "motivations": ["skill growth", "better career fit"],
            "work_style": "Prefers structured learning paths toward stated goals.",
        }
        logger.info("Using local persona fallback for user %s", user_id)
        return self._save_persona(user_id, data)

    def _save_persona(self, user_id: int, data: dict) -> dict:
        profile, _ = Profile.objects.get_or_create(user_id=user_id)
        rc = profile.resume_context or {}
        rc["persona"] = {
            "summary": data.get("summary", ""),
            "traits": data.get("traits", []) or [],
            "interests": data.get("interests", []) or [],
            "motivations": data.get("motivations", []) or [],
            "work_style": data.get("work_style", ""),
        }
        profile.resume_context = rc
        profile.save(update_fields=["resume_context"])
        return rc["persona"]
