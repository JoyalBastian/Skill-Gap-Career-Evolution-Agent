"""Gemini-only AI questionnaire orchestration."""
from __future__ import annotations

import logging
import threading

from django.db import IntegrityError, connection, transaction
from django.db.models import Max
from django.utils import timezone

from ai_engine.llm_client import GeminiUnavailable, LLMUnavailable, active_provider
from ai_engine.question_generator import (
    MAX_QUESTIONS,
    get_first_question,
    get_next_question,
    should_end_session,
)
from apps.questionnaire.models import AIAnswer, AIQuestion, QuestionnaireSession

logger = logging.getLogger(__name__)

_pipeline_lock = threading.Lock()
_pipeline_running_users: set[int] = set()


class QuestionnaireService:
    """AI-driven questionnaire orchestration. All questions come from Gemini."""

    def _get_session_for_update(self, session_pk: int) -> QuestionnaireSession:
        """Lock session row on Postgres; plain read on SQLite (avoids extra write locks)."""
        qs = QuestionnaireSession.objects
        if connection.vendor == "sqlite":
            return qs.get(pk=session_pk)
        return qs.select_for_update().get(pk=session_pk)

    def save_interview_prep(self, user, data: dict) -> None:
        """Persist pre-interview details so Gemini can tailor the first question."""
        from apps.users.models import Profile

        profile, _ = Profile.objects.get_or_create(user=user)
        rc = dict(profile.resume_context or {})

        prep = {
            "full_name": (data.get("full_name") or "").strip(),
            "current_title": (data.get("current_title") or "").strip(),
            "career_goals": (data.get("career_goals") or "").strip(),
            "focus_areas": (data.get("focus_areas") or "").strip(),
            "years_experience": data.get("years_experience") or "",
        }
        rc["interview_prep"] = prep

        if prep["full_name"]:
            rc["full_name"] = prep["full_name"]
        if prep["current_title"]:
            rc["current_title"] = prep["current_title"]
        if prep["years_experience"]:
            try:
                rc["experience_years"] = int(prep["years_experience"])
            except (TypeError, ValueError):
                pass

        profile.resume_context = rc
        if prep["career_goals"]:
            profile.bio = prep["career_goals"]
        level = (data.get("target_career_level") or "").strip()
        if level in dict(Profile.LEVEL_CHOICES):
            profile.target_career_level = level
        if "is_technical_track" in data:
            profile.is_technical_track = bool(data.get("is_technical_track"))
        profile.save(
            update_fields=[
                "resume_context",
                "bio",
                "target_career_level",
                "is_technical_track",
            ]
        )

    def start_session(self, user) -> QuestionnaireSession:
        QuestionnaireSession.objects.filter(
            user=user, status="in_progress"
        ).update(status="abandoned")

        session = QuestionnaireSession.objects.create(user=user, status="in_progress")

        if active_provider() == "ollama":
            from ai_engine.ollama_client import warmup_model
            warmup_model()

        resume_context = self._get_resume_context(user)
        first_q = get_first_question(resume_context=resume_context)  # may raise GeminiUnavailable
        AIQuestion.objects.create(
            session=session,
            order=1,
            text=first_q["text"],
            question_type=first_q["question_type"],
            options=first_q.get("options", []),
            topic=first_q.get("topic", "introduction"),
            source=first_q.get("source", "gemini"),
        )
        return session

    def get_current_question(self, session: QuestionnaireSession) -> AIQuestion | None:
        answered_ids = session.ai_answers.values_list("question_id", flat=True)
        return (
            session.ai_questions.exclude(id__in=answered_ids).order_by("order").first()
        )

    def save_answer(
        self,
        session: QuestionnaireSession,
        question: AIQuestion,
        text_response: str = "",
        selected_options: list[str] | None = None,
    ) -> AIAnswer:
        selected_options = selected_options or []
        with transaction.atomic():
            answer, _ = AIAnswer.objects.update_or_create(
                session=session,
                question=question,
                defaults={
                    "text_response": text_response,
                    "selected_options": selected_options,
                },
            )
        return answer

    def generate_next_question(self, session: QuestionnaireSession) -> AIQuestion | None:
        """Ask Gemini for the next question and persist it. May raise GeminiUnavailable."""
        with transaction.atomic():
            session = self._get_session_for_update(session.pk)

            answered_ids = set(
                session.ai_answers.values_list("question_id", flat=True)
            )
            pending = (
                session.ai_questions.exclude(id__in=answered_ids)
                .order_by("order")
                .first()
            )
            if pending:
                return pending

            history = self._build_history(session)
            max_order = session.ai_questions.aggregate(m=Max("order"))["m"] or 0
            next_order = max_order + 1

            if should_end_session(history, next_order):
                return None

            topics_covered = sorted(
                set(
                    session.ai_questions.exclude(topic="")
                    .values_list("topic", flat=True)
                )
            )
            questions_asked = list(
                session.ai_questions.order_by("order").values_list("text", flat=True)
            )
            resume_context = self._get_resume_context(session.user)

        # Gemini HTTP call outside transaction to avoid SQLite write lock during API wait
        question_data = get_next_question(
            history,
            next_order,
            resume_context=resume_context,
            topics_covered=topics_covered,
            questions_asked=questions_asked,
        )
        if not question_data:
            return None

        with transaction.atomic():
            session = self._get_session_for_update(session.pk)
            pending = (
                session.ai_questions.exclude(
                    id__in=session.ai_answers.values_list("question_id", flat=True)
                )
                .order_by("order")
                .first()
            )
            if pending:
                return pending
            if session.ai_questions.filter(order=next_order).exists():
                return session.ai_questions.filter(order=next_order).first()

            try:
                return AIQuestion.objects.create(
                    session=session,
                    order=next_order,
                    text=question_data["text"],
                    question_type=question_data.get("question_type", "single_choice"),
                    options=question_data.get("options", []),
                    topic=question_data.get("topic", "general"),
                    source=question_data.get("source", "gemini"),
                )
            except IntegrityError:
                existing = session.ai_questions.filter(order=next_order).first()
                if existing:
                    return existing
                raise

    def finalize_session(self, session_id: int) -> QuestionnaireSession:
        """Mark session completed without running the heavy AI pipeline."""
        session = QuestionnaireSession.objects.get(id=session_id)
        session.status = "completed"
        session.completed_at = timezone.now()
        session.save(update_fields=["status", "completed_at"])
        return session

    def run_analysis_pipeline(self, session_id: int) -> QuestionnaireSession:
        """Run post-interview AI analysis (multiple LLM calls — keep off request thread)."""
        if active_provider() == "ollama":
            from ai_engine.ollama_client import warmup_model
            warmup_model()

        session = QuestionnaireSession.objects.select_related("user").get(id=session_id)

        from services.career_prediction_service import CareerPredictionService
        from services.recommendation_service import RecommendationService
        from services.roadmap_service import RoadmapService
        from services.skill_gap_service import SkillGapService
        from services.user_understanding_service import UserUnderstandingService

        uus = UserUnderstandingService()
        try:
            uus.build_user_persona(session.user_id)
        except LLMUnavailable as e:
            logger.warning("Persona build failed, using local fallback: %s", e)
            if session.ai_answers.count() >= 3:
                uus.build_local_persona_fallback(session.user_id, session_id=session.id)

        try:
            CareerPredictionService().run_prediction(session.user_id)
        except LLMUnavailable as e:
            logger.warning("Career prediction failed for user=%s: %s", session.user_id, e)

        prediction = session.user.career_predictions.order_by("rank").first()
        gap_analyzed = False
        if prediction:
            try:
                SkillGapService().analyze_gaps(session.user_id, prediction.career_id)
                gap_analyzed = True
            except LLMUnavailable as e:
                logger.warning("Skill gap analysis failed for user=%s: %s", session.user_id, e)

        rec_svc = RecommendationService()
        if gap_analyzed:
            try:
                rec_svc.create_placeholder_gap_courses(session.user_id)
            except Exception as e:
                logger.warning(
                    "Placeholder courses failed for user=%s: %s",
                    session.user_id,
                    e,
                )
            rec_svc.start_generate_async(session.user_id, force=True)

        try:
            RoadmapService().generate_roadmap(session.user_id)
        except LLMUnavailable as e:
            logger.warning("Roadmap generation failed for user=%s: %s", session.user_id, e)

        return session

    def complete_session(self, session_id: int) -> QuestionnaireSession:
        """Finalize session and run full analysis pipeline synchronously."""
        self.finalize_session(session_id)
        return self.run_analysis_pipeline(session_id)

    @staticmethod
    def get_pipeline_status(user) -> dict:
        from apps.careers.models import CareerPrediction, SkillGapReport
        from apps.recommendations.models import Recommendation
        from apps.roadmap.models import Roadmap

        return {
            "predictions_ok": CareerPrediction.objects.filter(user=user).exists(),
            "gap_ok": SkillGapReport.objects.filter(user=user).exists(),
            "recs_ok": Recommendation.objects.filter(
                user=user,
                target_skill__isnull=False,
            ).exists(),
            "roadmap_ok": Roadmap.objects.filter(user=user).exists(),
        }

    @staticmethod
    def pipeline_is_complete(status: dict) -> bool:
        return all(status.values())

    def is_pipeline_running(self, user_id: int) -> bool:
        with _pipeline_lock:
            return user_id in _pipeline_running_users

    def start_analysis_pipeline_async(self, session_id: int) -> str:
        """
        Start background analysis if needed.
        Returns: 'done', 'running', or 'started'.
        """
        session = QuestionnaireSession.objects.select_related("user").get(id=session_id)
        user_id = session.user_id
        status = self.get_pipeline_status(session.user)
        if self.pipeline_is_complete(status):
            return "done"

        with _pipeline_lock:
            if user_id in _pipeline_running_users:
                return "running"
            _pipeline_running_users.add(user_id)

        def _run() -> None:
            try:
                self.run_analysis_pipeline(session_id)
            except Exception:
                logger.exception("Background analysis pipeline failed for session=%s", session_id)
            finally:
                with _pipeline_lock:
                    _pipeline_running_users.discard(user_id)

        threading.Thread(target=_run, daemon=True, name=f"pipeline-{session_id}").start()
        return "started"

    def get_progress(self, session: QuestionnaireSession) -> dict:
        answered = session.ai_answers.count()
        return {
            "answered": answered,
            "total": MAX_QUESTIONS,
            "percent": round((answered / MAX_QUESTIONS) * 100, 1),
        }

    def _build_history(self, session: QuestionnaireSession) -> list[dict]:
        history = []
        answers = (
            session.ai_answers
            .select_related("question")
            .order_by("question__order")
        )
        for ans in answers:
            history.append({
                "question": ans.question.text,
                "answer": ans.get_answer_text(),
                "topic": ans.question.topic,
            })
        return history

    def _get_resume_context(self, user) -> dict | None:
        from apps.users.models import Profile

        profile = Profile.objects.filter(user=user).first()
        if not profile:
            return None
        rc = dict(profile.resume_context or {})
        if profile.bio and not rc.get("interview_prep", {}).get("career_goals"):
            prep = dict(rc.get("interview_prep") or {})
            prep.setdefault("career_goals", profile.bio)
            rc["interview_prep"] = prep
        if rc:
            rc["target_career_level"] = profile.target_career_level
            rc["is_technical_track"] = profile.is_technical_track
            return rc
        if profile.bio or profile.target_career_level:
            return {
                "interview_prep": {"career_goals": profile.bio},
                "target_career_level": profile.target_career_level,
                "is_technical_track": profile.is_technical_track,
            }
        return None
