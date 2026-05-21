"""Trending jobs service — Gemini only.

`refresh_trending()` asks Gemini for a list of currently in-demand roles and
caches the response (24h) so we don't pay for it on every page load.

`match_for_user(user_id)` sends the current TrendingJob catalog + the user's
profile to Gemini and ranks them for that specific user.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.utils import timezone
from django.utils.text import slugify

from ai_engine.llm_client import GeminiUnavailable, chat_json
from apps.jobs.models import JobMatch, TrendingJob
from services.user_understanding_service import UserUnderstandingService

logger = logging.getLogger(__name__)

REFRESH_INTERVAL = timedelta(hours=24)
TRENDING_CACHE_KEY = "trending_jobs::v1"
VALID_DEMAND = {"low", "medium", "high", "very_high"}


def _ensure_slug(title: str, used: set[str]) -> str:
    base = slugify(title)[:60] or "job"
    s = base
    i = 2
    while s in used:
        s = f"{base}-{i}"[:80]
        i += 1
    used.add(s)
    return s


class TrendingJobsService:
    def refresh_trending(self, force: bool = False, top_n: int = 12) -> list[TrendingJob]:
        """Refresh the TrendingJob table from Gemini if data is stale."""
        if not force:
            newest = TrendingJob.objects.order_by("-refreshed_at").first()
            if newest and (timezone.now() - newest.refreshed_at) < REFRESH_INTERVAL:
                return list(TrendingJob.objects.all().order_by("title"))

        prompt = (
            "You are a labour market analyst. List the top job roles that are currently in highest "
            "demand globally in 2026, across technology, business, healthcare, design and other sectors.\n\n"
            f"Return EXACTLY {top_n} roles as a JSON object:\n"
            "{\n"
            "  \"jobs\": [\n"
            "    {\n"
            "      \"title\": \"Job title\",\n"
            "      \"summary\": \"2-3 sentence description of the role\",\n"
            "      \"demand_label\": \"low|medium|high|very_high\",\n"
            "      \"growth_reason\": \"1-2 sentences on why demand is growing\",\n"
            "      \"required_skills\": [\"skill1\", \"skill2\", \"...\"],\n"
            "      \"suggested_titles\": [\"alternative job title 1\", \"alternative job title 2\"],\n"
            "      \"salary_band_text\": \"qualitative band, e.g. 'High, varies by region'\"\n"
            "    }\n"
            "  ]\n"
            "}"
        )

        data = chat_json(prompt, cache_key=TRENDING_CACHE_KEY, ttl=REFRESH_INTERVAL)
        items = []
        if isinstance(data, dict):
            items = data.get("jobs") or []
        elif isinstance(data, list):
            items = data
        if not items:
            raise GeminiUnavailable("Gemini returned no trending jobs.")

        # Wipe the table to keep it small and fresh.
        TrendingJob.objects.all().delete()
        used_slugs: set[str] = set()
        created: list[TrendingJob] = []
        for item in items[:top_n]:
            if not isinstance(item, dict):
                continue
            title = (item.get("title") or "").strip()
            if not title:
                continue
            demand = item.get("demand_label") or "medium"
            if demand not in VALID_DEMAND:
                demand = "medium"
            job = TrendingJob.objects.create(
                title=title[:200],
                slug=_ensure_slug(title, used_slugs),
                summary=item.get("summary") or "",
                demand_label=demand,
                growth_reason=item.get("growth_reason") or "",
                required_skills=item.get("required_skills") or [],
                suggested_titles=item.get("suggested_titles") or [],
                salary_band_text=(item.get("salary_band_text") or "")[:120],
            )
            created.append(job)
        return created

    def match_for_user(self, user_id: int, top_n: int = 6) -> list[JobMatch]:
        """Ask Gemini to rank the current trending jobs for this user."""
        # Ensure we have a catalog
        jobs = list(TrendingJob.objects.all())
        if not jobs:
            jobs = self.refresh_trending()

        if not jobs:
            return []

        profile_text = UserUnderstandingService().get_user_profile_text(user_id)

        jobs_block = "\n".join(
            f"- [{j.slug}] {j.title}: {j.summary} (required: {', '.join(j.required_skills[:8])})"
            for j in jobs
        )

        prompt = (
            "You are a career matchmaker. Rank the trending jobs below by how well they suit this user.\n\n"
            f"USER PROFILE:\n{profile_text}\n\n"
            f"TRENDING JOBS:\n{jobs_block}\n\n"
            f"Return the TOP {top_n} jobs as a JSON object:\n"
            "{\n"
            "  \"matches\": [\n"
            "    {\n"
            "      \"job_slug\": \"slug from the list above\",\n"
            "      \"fit_score\": number from 0 to 100,\n"
            "      \"fit_reason\": \"1-2 sentences on why this job fits the user\",\n"
            "      \"matched_skills\": [\"skills the user already has that help\"],\n"
            "      \"missing_skills\": [\"skills the user should learn to qualify\"]\n"
            "    }\n"
            "  ]\n"
            "}"
        )

        data = chat_json(prompt)
        items = []
        if isinstance(data, dict):
            items = data.get("matches") or []
        elif isinstance(data, list):
            items = data
        if not items:
            return []

        # Clear previous matches for the user
        JobMatch.objects.filter(user_id=user_id).delete()

        slug_to_job = {j.slug: j for j in jobs}
        created: list[JobMatch] = []
        for item in items[:top_n]:
            if not isinstance(item, dict):
                continue
            slug = (item.get("job_slug") or "").strip().lower()
            job = slug_to_job.get(slug)
            if not job:
                # Try matching by title fallback
                title = (item.get("title") or "").strip().lower()
                for j in jobs:
                    if j.title.lower() == title:
                        job = j
                        break
            if not job:
                continue
            try:
                fit_score = float(item.get("fit_score") or 0)
            except (TypeError, ValueError):
                fit_score = 0.0
            match, _ = JobMatch.objects.update_or_create(
                user_id=user_id,
                job=job,
                defaults={
                    "fit_score": round(fit_score, 1),
                    "fit_reason": item.get("fit_reason") or "",
                    "matched_skills": item.get("matched_skills") or [],
                    "missing_skills": item.get("missing_skills") or [],
                },
            )
            created.append(match)
        return created
