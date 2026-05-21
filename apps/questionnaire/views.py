from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from ai_engine.llm_client import GeminiUnavailable, user_message_for
from apps.questionnaire.models import AIQuestion, QuestionnaireSession
from apps.users.models import Profile
from services.questionnaire_service import QuestionnaireService


def _prep_defaults(profile: Profile | None) -> dict:
    rc = (profile.resume_context or {}) if profile else {}
    prep = rc.get("interview_prep") or {}
    return {
        "full_name": prep.get("full_name") or rc.get("full_name", ""),
        "current_title": prep.get("current_title") or rc.get("current_title", ""),
        "career_goals": prep.get("career_goals") or (profile.bio if profile else ""),
        "focus_areas": prep.get("focus_areas", ""),
        "years_experience": prep.get("years_experience") or rc.get("experience_years", ""),
        "target_career_level": profile.target_career_level if profile else "beginner",
        "is_technical_track": profile.is_technical_track if profile else True,
    }


class QuestionnaireStartView(LoginRequiredMixin, View):
    """GET: collect information before the interview. POST: start AI question generation."""

    template_name = "questionnaire/intro.html"

    def get(self, request):
        profile = Profile.objects.filter(user=request.user).first()
        in_progress = QuestionnaireSession.objects.filter(
            user=request.user, status="in_progress"
        ).first()
        has_resume = bool(profile and profile.has_resume_context())
        return render(
            request,
            self.template_name,
            {
                "prep": _prep_defaults(profile),
                "level_choices": Profile.LEVEL_CHOICES,
                "in_progress_session": in_progress,
                "has_resume": has_resume,
            },
        )

    def post(self, request):
        career_goals = request.POST.get("career_goals", "").strip()
        if not career_goals:
            messages.warning(request, "Please describe your career goals before starting.")
            return redirect("questionnaire:start")

        svc = QuestionnaireService()
        svc.save_interview_prep(
            request.user,
            {
                "full_name": request.POST.get("full_name", ""),
                "current_title": request.POST.get("current_title", ""),
                "career_goals": career_goals,
                "focus_areas": request.POST.get("focus_areas", ""),
                "years_experience": request.POST.get("years_experience", ""),
                "target_career_level": request.POST.get("target_career_level", ""),
                "is_technical_track": request.POST.get("is_technical_track") == "on",
            },
        )

        try:
            session = svc.start_session(request.user)
        except GeminiUnavailable as e:
            messages.error(request, user_message_for(e))
            return redirect("questionnaire:start")

        return redirect("questionnaire:question", session_id=session.id)


class QuestionView(LoginRequiredMixin, View):
    template_name = "questionnaire/question.html"

    def get(self, request, session_id):
        session = get_object_or_404(QuestionnaireSession, id=session_id, user=request.user)

        if session.status == "completed":
            return redirect("questionnaire:complete", session_id=session.id)

        svc = QuestionnaireService()
        question = svc.get_current_question(session)

        if not question:
            return redirect("questionnaire:complete", session_id=session.id)

        return render(request, self.template_name, {
            "session": session,
            "question": question,
            "progress": svc.get_progress(session),
        })

    def post(self, request, session_id):
        session = get_object_or_404(QuestionnaireSession, id=session_id, user=request.user)

        if session.status == "completed":
            return redirect("questionnaire:complete", session_id=session.id)

        question = get_object_or_404(AIQuestion, id=request.POST.get("question_id"), session=session)

        svc = QuestionnaireService()

        text_response = request.POST.get("text_response", "").strip()
        selected_options = list(request.POST.getlist("options"))
        other_text = request.POST.get("other_text", "").strip()

        if question.question_type != "free_text":
            if "Other" in selected_options:
                if other_text:
                    selected_options = [
                        f"Other: {other_text}" if o == "Other" else o
                        for o in selected_options
                    ]
                else:
                    selected_options = [o for o in selected_options if o != "Other"]
            if not selected_options:
                messages.warning(
                    request,
                    "Please select an option or specify your answer under Other.",
                )
                return redirect("questionnaire:question", session_id=session.id)
        elif not text_response:
            messages.warning(request, "Please provide an answer before continuing.")
            return redirect("questionnaire:question", session_id=session.id)

        svc.save_answer(
            session,
            question,
            text_response=text_response,
            selected_options=selected_options,
        )

        try:
            next_question = svc.generate_next_question(session)
        except GeminiUnavailable as e:
            messages.error(
                request,
                user_message_for(e) + " Your answer was saved — please refresh to retry.",
            )
            return redirect("questionnaire:question", session_id=session.id)

        if next_question:
            return redirect("questionnaire:question", session_id=session.id)

        try:
            svc.complete_session(session.id)
        except GeminiUnavailable:
            messages.warning(
                request,
                "AI analysis was partially unavailable. Some results may be missing until you retry.",
            )
        return redirect("questionnaire:complete", session_id=session.id)


class QuestionnaireCompleteView(LoginRequiredMixin, View):
    template_name = "questionnaire/complete.html"

    def get(self, request, session_id):
        from apps.careers.models import CareerPrediction, SkillGapReport
        from apps.recommendations.models import Recommendation
        from apps.roadmap.models import Roadmap

        session = get_object_or_404(QuestionnaireSession, id=session_id, user=request.user)
        user = request.user
        pipeline_status = {
            "predictions_ok": CareerPrediction.objects.filter(user=user).exists(),
            "gap_ok": SkillGapReport.objects.filter(user=user).exists(),
            "recs_ok": Recommendation.objects.filter(user=user).exists(),
            "roadmap_ok": Roadmap.objects.filter(user=user).exists(),
        }
        return render(request, self.template_name, {
            "session": session,
            "pipeline_status": pipeline_status,
        })
