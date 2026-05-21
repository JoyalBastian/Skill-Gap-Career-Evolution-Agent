"""
User journey state for soft-gated UI flow.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from django.urls import reverse

NextStep = Literal[
    "resume",
    "interview",
    "predictions",
    "gaps",
    "roadmap",
    "recs",
    "jobs",
    "done",
]

# url_name -> minimum journey requirement (next_step must be past this)
PAGE_REQUIREMENTS: dict[str, NextStep] = {
    "careers:predictions": "predictions",
    "careers:list": "predictions",
    "careers:detail": "predictions",
    "skills:gap": "gaps",
    "skills:list": "predictions",
    "roadmap:list": "roadmap",
    "roadmap:detail": "roadmap",
    "recommendations:list": "recs",
    "jobs:trending": "jobs",
    "jobs:detail": "jobs",
    "progress:dashboard": "predictions",
    "analytics:dashboard": "predictions",
    "analytics:chat": "predictions",
}

STEP_ORDER: list[NextStep] = [
    "resume",
    "interview",
    "predictions",
    "gaps",
    "roadmap",
    "recs",
    "jobs",
    "done",
]

STEP_META: dict[NextStep, dict[str, str]] = {
    "resume": {
        "title": "Upload your resume",
        "description": "Let Gemini extract your skills and experience so we can personalize everything.",
        "cta": "Upload Resume",
        "icon": "file-earmark-arrow-up",
        "url_name": "users:resume_upload",
    },
    "interview": {
        "title": "Complete the AI interview",
        "description": "Answer a few questions about your goals, interests, and personality.",
        "cta": "Start AI Interview",
        "icon": "chat-square-dots",
        "url_name": "questionnaire:start",
    },
    "predictions": {
        "title": "View your career matches",
        "description": "See which careers Gemini recommends based on your profile.",
        "cta": "View Career Matches",
        "icon": "briefcase",
        "url_name": "careers:predictions",
    },
    "gaps": {
        "title": "Review your skill gaps",
        "description": "Find out what skills to build for your top career match.",
        "cta": "View Skill Gaps",
        "icon": "puzzle",
        "url_name": "skills:gap",
    },
    "roadmap": {
        "title": "Build your learning roadmap",
        "description": "Get a step-by-step plan to close your skill gaps.",
        "cta": "Open Roadmap",
        "icon": "map",
        "url_name": "roadmap:list",
    },
    "recs": {
        "title": "Explore recommendations",
        "description": "Courses and resources picked for your career path.",
        "cta": "View Recommendations",
        "icon": "lightbulb",
        "url_name": "recommendations:list",
    },
    "jobs": {
        "title": "Match trending jobs",
        "description": "See market-trending roles that fit your skills and interests.",
        "cta": "Browse Trending Jobs",
        "icon": "fire",
        "url_name": "jobs:trending",
    },
    "done": {
        "title": "You're all set",
        "description": "Track progress and refine your plan anytime.",
        "cta": "View Dashboard",
        "icon": "house-door",
        "url_name": "users:dashboard",
    },
}

# Human-readable labels for locked-page screens (url_name -> display)
PAGE_DISPLAY: dict[str, dict[str, str]] = {
    "careers:predictions": {"title": "Career Matches", "icon": "briefcase"},
    "careers:list": {"title": "Your Careers", "icon": "briefcase"},
    "careers:detail": {"title": "Career Details", "icon": "briefcase"},
    "skills:gap": {"title": "Skill Gaps", "icon": "puzzle"},
    "skills:list": {"title": "My Skills", "icon": "tags"},
    "roadmap:list": {"title": "Learning Roadmap", "icon": "map"},
    "roadmap:detail": {"title": "Roadmap Details", "icon": "map"},
    "recommendations:list": {"title": "Recommendations", "icon": "lightbulb"},
    "jobs:trending": {"title": "Trending Jobs", "icon": "fire"},
    "jobs:detail": {"title": "Job Details", "icon": "fire"},
    "progress:dashboard": {"title": "My Progress", "icon": "bar-chart-line"},
    "analytics:dashboard": {"title": "Analytics", "icon": "graph-up"},
    "analytics:chat": {"title": "AI Chat", "icon": "robot"},
}

# Checklist rows shown on locked pages (step key, journey attr, label)
JOURNEY_CHECKLIST: list[tuple[str, str, str]] = [
    ("resume", "has_resume", "Upload resume"),
    ("interview", "has_interview", "Complete AI interview"),
    ("predictions", "has_predictions", "Get career predictions"),
    ("gaps", "has_gap_report", "Review skill gaps"),
    ("roadmap", "has_roadmap", "Build learning roadmap"),
    ("recs", "has_recommendations", "View recommendations"),
    ("jobs", "has_trending_match", "Match trending jobs"),
]

# Sidebar url_names that can be locked
SIDEBAR_PAGES = {
    "careers:predictions": "predictions",
    "skills:gap": "gaps",
    "roadmap:list": "roadmap",
    "recommendations:list": "recs",
    "jobs:trending": "jobs",
    "progress:dashboard": "predictions",
    "analytics:dashboard": "predictions",
    "analytics:chat": "predictions",
}


@dataclass
class StepInfo:
    title: str
    description: str
    cta: str
    icon: str
    url: str


@dataclass
class JourneyState:
    has_resume: bool = False
    has_interview: bool = False
    has_predictions: bool = False
    has_gap_report: bool = False
    has_roadmap: bool = False
    has_recommendations: bool = False
    has_trending_match: bool = False
    next_step: NextStep = "resume"
    locked_pages: set[str] = field(default_factory=set)
    next_step_info: StepInfo | None = None
    employability_score: float | None = None
    sessions_count: int = 0
    predictions_count: int = 0

    def is_page_locked(self, url_name: str) -> bool:
        return url_name in self.locked_pages

    def is_sidebar_locked(self, url_name: str) -> bool:
        return url_name in self.locked_pages

    def checklist(self) -> list[dict]:
        """Rows for locked-page progress UI."""
        rows = []
        for step_key, attr, label in JOURNEY_CHECKLIST:
            done = bool(getattr(self, attr, False))
            rows.append({
                "step_key": step_key,
                "label": label,
                "done": done,
                "current": self.next_step == step_key and not done,
            })
        return rows


def page_display_for(url_name: str) -> dict[str, str]:
    return PAGE_DISPLAY.get(
        url_name,
        {"title": "This section", "icon": "lock"},
    )


def _step_index(step: NextStep) -> int:
    try:
        return STEP_ORDER.index(step)
    except ValueError:
        return 0


def _build_step_info(step: NextStep) -> StepInfo:
    meta = STEP_META[step]
    return StepInfo(
        title=meta["title"],
        description=meta["description"],
        cta=meta["cta"],
        icon=meta["icon"],
        url=reverse(meta["url_name"]),
    )


class JourneyService:
    @staticmethod
    def compute(user) -> JourneyState:
        if not user or not user.is_authenticated:
            return JourneyState()

        from apps.careers.models import CareerPrediction, SkillGapReport
        from apps.jobs.models import JobMatch
        from apps.questionnaire.models import QuestionnaireSession
        from apps.recommendations.models import Recommendation
        from apps.roadmap.models import Roadmap
        from apps.users.models import Profile, ResumeUpload

        profile = Profile.objects.filter(user=user).first()
        has_resume = bool(
            profile
            and profile.has_resume_context()
        ) or ResumeUpload.objects.filter(
            user=user, status="completed"
        ).exists()

        sessions_count = QuestionnaireSession.objects.filter(
            user=user, status="completed"
        ).count()
        has_interview = sessions_count > 0

        predictions_count = CareerPrediction.objects.filter(user=user).count()
        has_predictions = predictions_count > 0

        has_gap_report = SkillGapReport.objects.filter(user=user).exists()
        has_roadmap = Roadmap.objects.filter(user=user).exists()
        has_recommendations = Recommendation.objects.filter(user=user).exists()
        has_trending_match = JobMatch.objects.filter(user=user).exists()

        if not has_resume and not has_interview:
            next_step: NextStep = "resume"
        elif not has_interview:
            next_step = "interview"
        elif not has_predictions:
            next_step = "predictions"
        elif not has_gap_report:
            next_step = "gaps"
        elif not has_roadmap:
            next_step = "roadmap"
        elif not has_recommendations:
            next_step = "recs"
        elif not has_trending_match:
            next_step = "jobs"
        else:
            next_step = "done"

        locked_pages: set[str] = set()
        next_idx = _step_index(next_step)
        for url_name, required in PAGE_REQUIREMENTS.items():
            req_idx = _step_index(required)
            if next_idx < req_idx:
                locked_pages.add(url_name)

        # Also lock sidebar entries
        for url_name, required in SIDEBAR_PAGES.items():
            req_idx = _step_index(required)
            if next_idx < req_idx:
                locked_pages.add(url_name)

        gap_report = SkillGapReport.objects.filter(user=user).order_by("-created_at").first()
        employability = gap_report.employability_score if gap_report else None

        return JourneyState(
            has_resume=has_resume,
            has_interview=has_interview,
            has_predictions=has_predictions,
            has_gap_report=has_gap_report,
            has_roadmap=has_roadmap,
            has_recommendations=has_recommendations,
            has_trending_match=has_trending_match,
            next_step=next_step,
            locked_pages=locked_pages,
            next_step_info=_build_step_info(next_step) if next_step != "done" else None,
            employability_score=employability,
            sessions_count=sessions_count,
            predictions_count=predictions_count,
        )

    @staticmethod
    def get_for_request(request) -> JourneyState:
        if hasattr(request, "_journey"):
            return request._journey
        if request.user.is_authenticated:
            state = JourneyService.compute(request.user)
        else:
            state = JourneyState()
        request._journey = state
        return state
