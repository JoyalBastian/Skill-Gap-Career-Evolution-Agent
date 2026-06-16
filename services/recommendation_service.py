"""Gemini-only learning recommendation engine.

LearningResource rows are no longer seeded from JSON. Gemini suggests
resources per user; we save them with is_ai_generated=True so the UI can warn
the user that URLs may need verification.
"""
from __future__ import annotations

import logging
import threading

from django.db.models import Q

from ai_engine.llm_client import GeminiUnavailable, LLMUnavailable, active_provider, chat_json
from apps.analytics.models import AIInsight
from apps.careers.models import SkillGapReport
from apps.recommendations.models import LearningResource, Recommendation
from apps.users.models import Profile
from services.skill_utils import ensure_skill
from services.user_understanding_service import UserUnderstandingService

logger = logging.getLogger(__name__)

VALID_TYPES = {"course", "certification", "project", "technology", "book"}
VALID_LEVELS = {"beginner", "intermediate", "advanced"}

_rec_lock = threading.Lock()
_rec_running_users: set[int] = set()


def _valid_url(url: str) -> str:
    u = (url or "").strip()
    if u.startswith("https://"):
        return u
    return ""


class RecommendationService:
    def default_top_n(self) -> int:
        return 4 if active_provider() == "ollama" else 12

    def _ollama_max_tokens(self, top_n: int) -> int:
        # Large JSON payloads; 2048 often truncates mid-array on local models.
        return 4096 if top_n > 3 else 3072

    def _is_truncated_json_error(self, exc: BaseException) -> bool:
        msg = str(exc).lower()
        return "valid json" in msg or "truncated" in msg

    def is_generating(self, user_id: int) -> bool:
        with _rec_lock:
            return user_id in _rec_running_users

    def start_generate_async(self, user_id: int) -> str:
        """
        Generate recommendations in a background thread.
        Returns: 'done', 'running', or 'started'.
        """
        if Recommendation.objects.filter(user_id=user_id).exists():
            return "done"

        with _rec_lock:
            if user_id in _rec_running_users:
                return "running"
            _rec_running_users.add(user_id)

        def _run() -> None:
            try:
                self.generate_recommendations(user_id)
            except Exception:
                logger.exception(
                    "Background recommendation generation failed for user=%s",
                    user_id,
                )
            finally:
                with _rec_lock:
                    _rec_running_users.discard(user_id)

        threading.Thread(
            target=_run,
            daemon=True,
            name=f"recs-{user_id}",
        ).start()
        return "started"

    def generate_recommendations(
        self,
        user_id: int,
        top_n: int | None = None,
    ) -> list[Recommendation]:
        top_n = top_n or self.default_top_n()
        profile_text = UserUnderstandingService().get_user_profile_text(user_id)

        profile = Profile.objects.filter(user_id=user_id).first()
        user_level = (profile.target_career_level if profile else "beginner") or "beginner"

        gap_report = SkillGapReport.objects.filter(user_id=user_id).first()
        gap_skills: list[str] = []
        if gap_report:
            for s in gap_report.prioritized_skills or []:
                name = s.get("skill") or s.get("skill_name")
                if name:
                    gap_skills.append(name)

        gap_section = ""
        if gap_skills:
            gap_section = (
                "TOP SKILL GAPS (each recommendation MUST help close at least one):\n"
                + ", ".join(gap_skills[:8])
                + "\n\n"
            )

        def build_prompt(n: int) -> str:
            return (
                "You are a learning coach. Recommend personalized learning resources for the user.\n\n"
                "STRICT RULES:\n"
                f"1. Return EXACTLY {n} items in the recommendations array.\n"
                "2. Each item must address at least one of the user's gap skills (if listed).\n"
                "3. reason must cite a specific user need (gap skill, career goal, or profile fact).\n"
                "4. title max 80 characters; description max 120 characters; score between 0 and 1.\n"
                "5. resource_type must be one of: course, certification, project, technology, book.\n"
                "6. level must be one of: beginner, intermediate, advanced.\n"
                "7. Only include url if you are confident it is a real https URL; otherwise use empty string.\n"
                "8. Do not duplicate titles.\n\n"
                f"USER PROFILE:\n{profile_text}\n\n"
                f"USER LEVEL: {user_level}\n"
                f"{gap_section}"
                f"Return EXACTLY {n} resources mixing courses, certifications, projects and books.\n"
                "Respond ONLY with a JSON object:\n"
                "{\n"
                "  \"recommendations\": [\n"
                "    {\n"
                "      \"title\": \"resource title\",\n"
                "      \"resource_type\": \"course|certification|project|technology|book\",\n"
                "      \"level\": \"beginner|intermediate|advanced\",\n"
                "      \"description\": \"short summary\",\n"
                "      \"url\": \"https://... or empty\",\n"
                "      \"skills\": [\"skills covered\"],\n"
                "      \"score\": number from 0 to 1,\n"
                "      \"reason\": \"why this fits this user\"\n"
                "    }\n"
                "  ]\n"
                "}"
            )

        attempt_counts = [top_n]
        if active_provider() == "ollama" and top_n > 3:
            attempt_counts.append(3)

        data = None
        last_exc: BaseException | None = None
        for attempt_n in attempt_counts:
            max_tokens = (
                self._ollama_max_tokens(attempt_n)
                if active_provider() == "ollama"
                else 3072
            )
            try:
                data = chat_json(build_prompt(attempt_n), max_output_tokens=max_tokens)
                top_n = attempt_n
                break
            except LLMUnavailable as e:
                last_exc = e
                if not self._is_truncated_json_error(e):
                    raise
                logger.warning(
                    "Recommendations JSON truncated (n=%s); retrying with fewer items.",
                    attempt_n,
                )
        if data is None:
            raise last_exc or GeminiUnavailable("Failed to generate recommendations.")
        items = []
        if isinstance(data, dict):
            items = data.get("recommendations") or []
        elif isinstance(data, list):
            items = data
        if not items:
            raise GeminiUnavailable("Gemini returned no recommendations.")

        Recommendation.objects.filter(user_id=user_id).delete()

        created: list[Recommendation] = []
        seen_keys: set[tuple[str, str]] = set()

        for item in items:
            if not isinstance(item, dict):
                continue
            title = (item.get("title") or "").strip()[:120]
            if not title:
                continue

            rtype = item.get("resource_type") or "course"
            if rtype not in VALID_TYPES:
                rtype = "course"
            level = item.get("level") or "beginner"
            if level not in VALID_LEVELS:
                level = "beginner"

            dedupe_key = (title.lower(), rtype)
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)

            score = float(item.get("score") or 0.5)
            score = max(0.0, min(1.0, score))
            url = _valid_url(item.get("url") or "")
            reason = item.get("reason") or ""

            resource, _ = LearningResource.objects.get_or_create(
                title=title[:255],
                resource_type=rtype,
                defaults={
                    "description": (item.get("description") or "")[:2000],
                    "url": url,
                    "level": level,
                    "is_ai_generated": True,
                },
            )
            updated = False
            if not resource.description and item.get("description"):
                resource.description = item["description"][:2000]
                updated = True
            if not resource.url and url:
                resource.url = url
                updated = True
            if updated:
                resource.save(update_fields=["description", "url"])

            for skill_name in item.get("skills") or []:
                sk = ensure_skill(str(skill_name).strip())
                if sk:
                    resource.skills.add(sk)

            rec = Recommendation.objects.create(
                user_id=user_id,
                resource=resource,
                category=rtype,
                title=resource.title,
                description=resource.description,
                url=resource.url,
                score=round(score, 3),
                reason=reason,
            )
            created.append(rec)
            if len(created) >= top_n:
                break

        if not created:
            raise GeminiUnavailable("No valid recommendations after validation.")

        AIInsight.objects.create(
            user_id=user_id,
            insight_type="recommendation",
            payload={"count": len(created)},
        )
        return created

    def get_user_recommendations(self, user_id: int, category: str | None = None):
        qs = Recommendation.objects.filter(user_id=user_id)
        if category:
            qs = qs.filter(category=category)
        return qs.order_by("-score")

    def get_recommendations_for_skill(
        self,
        user_id: int,
        skill_name: str,
        skill_slug: str,
    ):
        """Recommendations linked to or mentioning a specific gap skill."""
        qs = Recommendation.objects.filter(user_id=user_id).select_related("resource")
        return qs.filter(
            Q(resource__skills__slug=skill_slug)
            | Q(resource__skills__name__iexact=skill_name)
            | Q(reason__icontains=skill_name)
            | Q(title__icontains=skill_name)
        ).distinct().order_by("-score")
