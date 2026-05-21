from django.shortcuts import render

from apps.users.services.journey_service import (
    PAGE_REQUIREMENTS,
    JourneyService,
    page_display_for,
)

REQUIREMENTS_LABEL = {
    "resume": "uploading your resume",
    "interview": "the AI interview",
    "predictions": "career predictions",
    "gaps": "skill gap analysis",
    "roadmap": "a learning roadmap",
    "recs": "recommendations",
    "jobs": "trending job matches",
}


def render_locked_page(request, url_name: str, *, locked_title: str = "", locked_body: str = ""):
    """Full layout (navbar + sidebar) with locked-state content."""
    journey = JourneyService.get_for_request(request)
    display = page_display_for(url_name)
    required = PAGE_REQUIREMENTS.get(url_name, "predictions")
    return render(
        request,
        "users/locked_page.html",
        {
            "journey": journey,
            "page_title": display["title"],
            "page_icon": display["icon"],
            "locked_title": locked_title or "Complete earlier steps first",
            "locked_body": locked_body or (
                "This section unlocks once you finish the recommended steps "
                "in your career journey."
            ),
            "journey_checklist": journey.checklist(),
            "required_step_label": REQUIREMENTS_LABEL.get(required, "earlier steps"),
        },
    )


class LockedPageMixin:
    """Soft-gate for class-based views with render_to_response (e.g. ListView)."""

    page_url_name: str | None = None
    locked_title: str = ""
    locked_body: str = ""

    def get_page_url_name(self) -> str | None:
        if self.page_url_name:
            return self.page_url_name
        match = getattr(self.request, "resolver_match", None)
        if match and match.url_name and match.namespace:
            return f"{match.namespace}:{match.url_name}"
        if match and match.url_name:
            return match.url_name
        return None

    def dispatch(self, request, *args, **kwargs):
        journey = JourneyService.get_for_request(request)
        url_name = self.get_page_url_name()
        self._page_locked = bool(url_name and journey.is_page_locked(url_name))
        self._locked_url_name = url_name or ""
        return super().dispatch(request, *args, **kwargs)

    def render_to_response(self, context, **response_kwargs):
        if getattr(self, "_page_locked", False):
            return render_locked_page(
                self.request,
                self._locked_url_name,
                locked_title=self.locked_title,
                locked_body=self.locked_body,
            )
        return super().render_to_response(context, **response_kwargs)


class JourneyGatedViewMixin:
    """Soft-gate for django.views.View subclasses."""

    page_url_name: str | None = None
    locked_title: str = ""
    locked_body: str = ""

    def get_page_url_name(self) -> str | None:
        if self.page_url_name:
            return self.page_url_name
        match = getattr(self.request, "resolver_match", None)
        if match and match.url_name and match.namespace:
            return f"{match.namespace}:{match.url_name}"
        if match and match.url_name:
            return match.url_name
        return None

    def dispatch(self, request, *args, **kwargs):
        self.request = request
        journey = JourneyService.get_for_request(request)
        url_name = self.get_page_url_name()
        if url_name and journey.is_page_locked(url_name):
            return render_locked_page(
                request,
                url_name,
                locked_title=self.locked_title,
                locked_body=self.locked_body,
            )
        return super().dispatch(request, *args, **kwargs)
