"""Gemini-powered chatbot service."""
from __future__ import annotations

import logging

from ai_engine.llm_client import GeminiUnavailable, chat_text, is_enabled, user_message_for
from apps.analytics.models import ChatMessage
from apps.careers.models import CareerPrediction, SkillGapReport
from apps.users.models import Profile
from services.career_prediction_service import CareerPredictionService

logger = logging.getLogger(__name__)


class ChatbotService:
    def is_enabled(self) -> bool:
        return is_enabled()

    def _profile_context(self, user_id: int) -> dict:
        profile = Profile.objects.filter(user_id=user_id).first()
        rc = (profile.resume_context or {}) if profile else {}
        predictions = list(CareerPredictionService().get_latest(user_id)[:3])
        gap_report = SkillGapReport.objects.filter(user_id=user_id).first()
        missing = []
        if gap_report:
            missing = [
                s.get("skill_name", "")
                for s in (gap_report.missing_skills or [])[:5]
                if s.get("skill_name")
            ]
        return {
            "rc": rc,
            "predictions": predictions,
            "missing": missing,
            "employability": gap_report.employability_score if gap_report else None,
        }

    def _build_compact_prompt(self, user_id: int, user_message: str) -> str:
        """Smaller prompt to reduce tokens and quota usage."""
        ctx = self._profile_context(user_id)
        rc = ctx["rc"]
        lines = [
            "You are SkillGap AI career counselor. Be concise (under 180 words).",
        ]
        if rc.get("current_title"):
            lines.append(f"Role: {rc['current_title']}")
        if rc.get("skills"):
            lines.append(f"Skills: {', '.join(rc['skills'][:8])}")
        if ctx["predictions"]:
            top = ", ".join(f"{p.career.name} ({p.confidence_pct}%)" for p in ctx["predictions"][:3])
            lines.append(f"Top careers: {top}")
        if ctx["missing"]:
            lines.append(f"Skill gaps: {', '.join(ctx['missing'])}")
        if ctx["employability"] is not None:
            lines.append(f"Employability: {ctx['employability']:.0f}%")

        history = list(
            ChatMessage.objects.filter(user_id=user_id)
            .order_by("-created_at")[:6]
        )
        history.reverse()
        if history:
            lines.append("\nRecent chat:")
            for msg in history:
                role = "User" if msg.role == "user" else "Assistant"
                text = (msg.content or "")[:400]
                lines.append(f"{role}: {text}")

        lines.append(f"\nUser: {user_message}")
        lines.append("Assistant:")
        return "\n".join(lines)

    def _offline_reply(self, user_id: int, user_message: str, exc: GeminiUnavailable) -> str:
        """Helpful reply from local data when Gemini quota is exhausted."""
        ctx = self._profile_context(user_id)
        rc = ctx["rc"]
        parts = [
            "I cannot reach Gemini right now because your API quota is exhausted.",
            user_message_for(exc),
            "",
            "Based on your saved profile:",
        ]
        if rc.get("current_title"):
            parts.append(f"- Current role: {rc['current_title']}")
        if ctx["predictions"]:
            parts.append(
                "- Top career match: "
                + f"{ctx['predictions'][0].career.name} ({ctx['predictions'][0].confidence_pct}%)"
            )
        if ctx["missing"]:
            parts.append(f"- Focus on learning: {', '.join(ctx['missing'][:3])}")
        parts.append(
            "\nTry again after the quota resets, change GEMINI_MODEL in backend/.env, "
            "or enable billing at https://aistudio.google.com/"
        )
        return "\n".join(parts)

    def send_message(self, user_id: int, message: str) -> str:
        ChatMessage.objects.create(user_id=user_id, role="user", content=message)

        prompt = self._build_compact_prompt(user_id, message)

        try:
            reply = chat_text(prompt)
        except GeminiUnavailable as e:
            logger.error("Chatbot Gemini call failed: %s", e)
            if e.is_quota or e.code == 429:
                reply = self._offline_reply(user_id, message, e)
            else:
                reply = user_message_for(e)

        ChatMessage.objects.create(user_id=user_id, role="assistant", content=reply)
        return reply

    def get_history(self, user_id: int):
        return ChatMessage.objects.filter(user_id=user_id).order_by("created_at")
