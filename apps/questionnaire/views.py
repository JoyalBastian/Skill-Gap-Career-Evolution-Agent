from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.decorators.http import require_POST
from django.utils.decorators import method_decorator

from ai_engine.llm_client import GeminiUnavailable, LLMUnavailable, user_message_for
from apps.questionnaire.models import AIQuestion, QuestionnaireSession
from apps.users.models import Profile
from services.questionnaire_service import QuestionnaireService


def _user_display_name(user) -> str:
    """Prefer profile/resume name; fall back to the logged-in account."""
    name = user.get_full_name().strip()
    if name:
        return name
    username = (user.username or "").strip()
    if not username:
        return "there"
    if " " not in username and ("_" in username or "." in username):
        return username.replace("_", " ").replace(".", " ").title()
    return username


def _prep_defaults(profile: Profile | None, user) -> dict:
    rc = (profile.resume_context or {}) if profile else {}
    prep = rc.get("interview_prep") or {}
    stored_name = (prep.get("full_name") or rc.get("full_name") or "").strip()
    return {
        "full_name": stored_name or _user_display_name(user),
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
        prep = _prep_defaults(profile, request.user)
        return render(
            request,
            self.template_name,
            {
                "prep": prep,
                "display_name": prep["full_name"],
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

        profile = Profile.objects.filter(user=request.user).first()
        prep = _prep_defaults(profile, request.user)

        svc = QuestionnaireService()
        svc.save_interview_prep(
            request.user,
            {
                "full_name": prep["full_name"],
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
        except (GeminiUnavailable, LLMUnavailable) as e:
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
        except (GeminiUnavailable, LLMUnavailable) as e:
            messages.error(
                request,
                user_message_for(e) + " Your answer was saved — please refresh to retry.",
            )
            return redirect("questionnaire:question", session_id=session.id)

        if next_question:
            return redirect("questionnaire:question", session_id=session.id)

        svc.finalize_session(session.id)
        return redirect("questionnaire:complete", session_id=session.id)


class PipelineRunView(LoginRequiredMixin, View):
    """Start background post-interview analysis (returns immediately)."""

    @method_decorator(require_POST)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)

    def post(self, request, session_id):
        session = get_object_or_404(
            QuestionnaireSession,
            id=session_id,
            user=request.user,
            status="completed",
        )
        svc = QuestionnaireService()
        state = svc.start_analysis_pipeline_async(session.id)
        status = svc.get_pipeline_status(request.user)
        return JsonResponse({
            "state": state,
            "pipeline_status": status,
            "complete": svc.pipeline_is_complete(status),
        })


class PipelineStatusView(LoginRequiredMixin, View):
    """Poll analysis pipeline progress."""

    def get(self, request, session_id):
        session = get_object_or_404(
            QuestionnaireSession,
            id=session_id,
            user=request.user,
            status="completed",
        )
        svc = QuestionnaireService()
        status = svc.get_pipeline_status(request.user)
        return JsonResponse({
            "pipeline_status": status,
            "complete": svc.pipeline_is_complete(status),
            "running": svc.is_pipeline_running(session.user_id),
        })


class QuestionnaireCompleteView(LoginRequiredMixin, View):
    template_name = "questionnaire/complete.html"

    def get(self, request, session_id):
        session = get_object_or_404(QuestionnaireSession, id=session_id, user=request.user)
        svc = QuestionnaireService()
        pipeline_status = svc.get_pipeline_status(request.user)
        return render(request, self.template_name, {
            "session": session,
            "pipeline_status": pipeline_status,
            "pipeline_complete": svc.pipeline_is_complete(pipeline_status),
            "pipeline_running": svc.is_pipeline_running(request.user.id),
        })
