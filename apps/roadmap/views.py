from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from ai_engine.llm_client import GeminiUnavailable, user_message_for
from apps.careers.models import CareerDomain
from apps.users.mixins import JourneyGatedViewMixin
from services.progress_service import ProgressService
from services.roadmap_service import RoadmapService

from .models import Roadmap, RoadmapStep


class RoadmapListView(JourneyGatedViewMixin, LoginRequiredMixin, View):
    page_url_name = "roadmap:list"
    template_name = "roadmap/list.html"

    def get(self, request):
        roadmaps = Roadmap.objects.filter(user=request.user).order_by("-generated_at")
        active = RoadmapService().get_active_roadmap(request.user.id)
        careers = CareerDomain.objects.all()
        return render(request, self.template_name, {
            "roadmaps": roadmaps,
            "active_roadmap": active,
            "careers": careers,
        })

    def post(self, request):
        career_id = request.POST.get("career_id")
        level = request.POST.get("level", "beginner")
        try:
            roadmap = RoadmapService().generate_roadmap(
                request.user.id,
                career_id=int(career_id) if career_id else None,
                level=level,
            )
        except GeminiUnavailable as e:
            messages.error(request, user_message_for(e))
            return redirect("roadmap:list")

        if not roadmap:
            messages.warning(
                request,
                "No target career available yet. Complete the AI interview or upload a resume first.",
            )
            return redirect("roadmap:list")

        messages.success(request, f"Roadmap '{roadmap.title}' generated.")
        return redirect("roadmap:detail", pk=roadmap.id)


class RoadmapDetailView(JourneyGatedViewMixin, LoginRequiredMixin, View):
    page_url_name = "roadmap:detail"
    template_name = "roadmap/detail.html"

    def get(self, request, pk):
        roadmap = get_object_or_404(Roadmap, pk=pk, user=request.user)
        from apps.progress.models import ProgressEntry
        from django.contrib.contenttypes.models import ContentType

        ct = ContentType.objects.get_for_model(RoadmapStep)
        completed = set(
            int(oid) for oid in
            ProgressEntry.objects.filter(
                user=request.user, content_type=ct, is_completed=True
            ).values_list("object_id", flat=True)
        )
        steps = list(roadmap.steps.prefetch_related("skills"))
        total = len(steps)
        done = len([s for s in steps if s.id in completed])
        progress_pct = round((done / total) * 100) if total else 0
        return render(request, self.template_name, {
            "roadmap": roadmap,
            "steps": steps,
            "completed_step_ids": completed,
            "progress_pct": progress_pct,
            "done_count": done,
            "total_count": total,
        })


class MarkStepCompleteView(LoginRequiredMixin, View):
    def post(self, request, step_id):
        ProgressService().mark_step_complete(request.user.id, step_id)
        step = get_object_or_404(RoadmapStep, id=step_id, roadmap__user=request.user)
        messages.success(request, f"Completed: {step.title}")
        return redirect("roadmap:detail", pk=step.roadmap_id)
