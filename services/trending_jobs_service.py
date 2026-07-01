"""Trending jobs service — works with Gemini or Ollama (AI_PROVIDER).

`refresh_trending()` fetches in-demand roles from the configured provider and
caches them (24h). Falls back to built-in seed data when the model times out.

`match_for_user(user_id)` ranks trending jobs for the user. Falls back to
rule-based matching from career predictions and profile skills when the model
is unavailable or the analysis pipeline is already using Ollama.
"""
from __future__ import annotations

import logging
import re
import threading
from datetime import timedelta
from difflib import SequenceMatcher

from django.utils import timezone
from django.utils.text import slugify

from ai_engine.llm_client import LLMUnavailable, active_provider, chat_json
from apps.careers.models import CareerPrediction
from apps.jobs.models import JobMatch, TrendingJob
from apps.users.models import Profile
from services.user_understanding_service import UserUnderstandingService

logger = logging.getLogger(__name__)

REFRESH_INTERVAL = timedelta(hours=24)
TRENDING_CACHE_KEY = "trending_jobs::v1"
VALID_DEMAND = {"low", "medium", "high", "very_high"}

_task_lock = threading.Lock()
_task_running_users: set[int] = set()

_DEFAULT_TRENDING_JOBS: list[dict] = [
    {
        "title": "Software Engineer",
        "summary": "Designs, builds, and maintains software applications across web, mobile, and cloud platforms.",
        "demand_label": "very_high",
        "growth_reason": "Digital transformation continues to drive demand for developers worldwide.",
        "required_skills": ["Programming", "Git", "APIs", "Problem Solving", "SQL"],
        "suggested_titles": ["Backend Developer", "Full Stack Engineer"],
        "salary_band_text": "High, varies by region and seniority",
    },
    {
        "title": "Data Analyst",
        "summary": "Turns raw data into insights that guide business decisions using SQL, spreadsheets, and visualization tools.",
        "demand_label": "high",
        "growth_reason": "Organizations rely on data-driven decision making across every industry.",
        "required_skills": ["SQL", "Excel", "Data Visualization", "Statistics", "Communication"],
        "suggested_titles": ["Business Analyst", "Analytics Specialist"],
        "salary_band_text": "Medium to high",
    },
    {
        "title": "Machine Learning Engineer",
        "summary": "Builds and deploys ML models for prediction, automation, and intelligent products.",
        "demand_label": "very_high",
        "growth_reason": "AI adoption is accelerating across products and enterprise workflows.",
        "required_skills": ["Python", "Machine Learning", "Statistics", "MLOps", "Deep Learning"],
        "suggested_titles": ["AI Engineer", "ML Scientist"],
        "salary_band_text": "Very high",
    },
    {
        "title": "Cloud Engineer",
        "summary": "Designs and operates cloud infrastructure on AWS, Azure, or GCP with a focus on reliability and cost.",
        "demand_label": "high",
        "growth_reason": "Cloud migration and SaaS growth sustain strong hiring for cloud talent.",
        "required_skills": ["Linux", "Docker", "Kubernetes", "Networking", "Terraform"],
        "suggested_titles": ["DevOps Engineer", "Platform Engineer"],
        "salary_band_text": "High",
    },
    {
        "title": "Product Manager",
        "summary": "Defines product strategy, prioritizes roadmaps, and aligns engineering with user needs.",
        "demand_label": "high",
        "growth_reason": "Tech and non-tech companies need PMs to ship customer-centric products.",
        "required_skills": ["Product Strategy", "User Research", "Roadmapping", "Communication", "Analytics"],
        "suggested_titles": ["Technical Product Manager", "Product Owner"],
        "salary_band_text": "High",
    },
    {
        "title": "UX Designer",
        "summary": "Creates user-centered interfaces through research, wireframes, and usability testing.",
        "demand_label": "medium",
        "growth_reason": "Product quality and accessibility expectations continue to rise.",
        "required_skills": ["User Research", "Wireframing", "Figma", "Prototyping", "Usability Testing"],
        "suggested_titles": ["UI Designer", "Product Designer"],
        "salary_band_text": "Medium to high",
    },
    {
        "title": "Cybersecurity Analyst",
        "summary": "Protects systems and data by monitoring threats, hardening infrastructure, and responding to incidents.",
        "demand_label": "high",
        "growth_reason": "Rising cyber threats increase demand for security specialists.",
        "required_skills": ["Networking", "Security Fundamentals", "Risk Assessment", "Scripting", "SIEM"],
        "suggested_titles": ["Security Engineer", "SOC Analyst"],
        "salary_band_text": "High",
    },
    {
        "title": "Digital Marketing Specialist",
        "summary": "Runs campaigns across search, social, and email to grow brands and acquire customers.",
        "demand_label": "medium",
        "growth_reason": "Digital channels dominate marketing spend and require skilled operators.",
        "required_skills": ["SEO", "Content Strategy", "Analytics", "Social Media", "Copywriting"],
        "suggested_titles": ["Growth Marketer", "Marketing Coordinator"],
        "salary_band_text": "Medium",
    },
]


def _ensure_slug(title: str, used: set[str]) -> str:
    base = slugify(title)[:60] or "job"
    slug = base
    index = 2
    while slug in used:
        slug = f"{base}-{index}"[:80]
        index += 1
    used.add(slug)
    return slug


def _is_truncated_json_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "valid json" in msg or "truncated" in msg or "timed out" in msg


def _normalize_token(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _text_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, _normalize_token(a), _normalize_token(b)).ratio()


def _pipeline_using_ollama(user_id: int) -> bool:
    if active_provider() != "ollama":
        return False
    try:
        from services.questionnaire_service import QuestionnaireService

        return QuestionnaireService().is_pipeline_running(user_id)
    except Exception:
        return False


class TrendingJobsService:
    def default_trending_count(self) -> int:
        return 6 if active_provider() == "ollama" else 12

    def default_match_count(self) -> int:
        return 4 if active_provider() == "ollama" else 6

    def is_running(self, user_id: int) -> bool:
        with _task_lock:
            return user_id in _task_running_users

    def start_async(self, user_id: int, *, refresh_trends: bool = False) -> str:
        """Run trending refresh and/or user matching off the request thread."""
        with _task_lock:
            if user_id in _task_running_users:
                return "running"
            _task_running_users.add(user_id)

        def _run() -> None:
            try:
                if refresh_trends:
                    self.refresh_trending(force=True)
                self.match_for_user(user_id)
            except Exception as exc:
                logger.warning(
                    "Background trending jobs task failed for user=%s: %s",
                    user_id,
                    exc,
                )
            finally:
                with _task_lock:
                    _task_running_users.discard(user_id)

        threading.Thread(
            target=_run,
            daemon=True,
            name=f"trending-jobs-{user_id}",
        ).start()
        return "started"

    def _persist_trending_jobs(self, items: list[dict], top_n: int) -> list[TrendingJob]:
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
                summary=(item.get("summary") or "")[:2000],
                demand_label=demand,
                growth_reason=(item.get("growth_reason") or "")[:2000],
                required_skills=item.get("required_skills") or [],
                suggested_titles=item.get("suggested_titles") or [],
                salary_band_text=(item.get("salary_band_text") or "")[:120],
            )
            created.append(job)
        return created

    def seed_default_trending_jobs(self, top_n: int | None = None) -> list[TrendingJob]:
        top_n = top_n or self.default_trending_count()
        logger.info("Seeding default trending jobs catalog (%s roles)", top_n)
        return self._persist_trending_jobs(_DEFAULT_TRENDING_JOBS, top_n)

    def refresh_trending(self, force: bool = False, top_n: int | None = None) -> list[TrendingJob]:
        """Refresh the TrendingJob table from the AI provider if data is stale."""
        top_n = top_n or self.default_trending_count()
        if not force:
            newest = TrendingJob.objects.order_by("-refreshed_at").first()
            if newest and (timezone.now() - newest.refreshed_at) < REFRESH_INTERVAL:
                return list(TrendingJob.objects.all().order_by("title"))

        is_ollama = active_provider() == "ollama"
        summary_rule = "summary max 80 characters.\n" if is_ollama else ""
        prompt = (
            "You are a labour market analyst. List job roles currently in high demand globally.\n\n"
            f"Return EXACTLY {top_n} roles as a JSON object:\n"
            "{\n"
            '  "jobs": [\n'
            "    {\n"
            '      "title": "Job title",\n'
            f'      "summary": "short description",\n'
            '      "demand_label": "low|medium|high|very_high",\n'
            '      "growth_reason": "why demand is growing",\n'
            '      "required_skills": ["skill1", "skill2"],\n'
            '      "suggested_titles": ["alt title 1"],\n'
            '      "salary_band_text": "qualitative band"\n'
            "    }\n"
            "  ]\n"
            "}\n"
            f"{summary_rule}"
        )

        try:
            data = chat_json(
                prompt,
                cache_key=TRENDING_CACHE_KEY,
                ttl=REFRESH_INTERVAL,
                max_output_tokens=3072 if is_ollama else 4096,
                provider=active_provider(),
            )
            items: list = []
            if isinstance(data, dict):
                items = data.get("jobs") or []
            elif isinstance(data, list):
                items = data
            if items:
                created = self._persist_trending_jobs(items, top_n)
                if created:
                    return created
        except LLMUnavailable as exc:
            logger.warning("Trending refresh LLM failed, using seed catalog: %s", exc)

        existing = list(TrendingJob.objects.all().order_by("title"))
        if existing and not force:
            return existing
        return self.seed_default_trending_jobs(top_n)

    def _build_jobs_block(self, jobs: list[TrendingJob], limit: int) -> str:
        lines: list[str] = []
        for job in jobs[:limit]:
            skills = ", ".join((job.required_skills or [])[:5])
            summary = (job.summary or "")[:80]
            lines.append(f"- [{job.slug}] {job.title}: {summary} (skills: {skills})")
        return "\n".join(lines)

    def _fetch_user_matches(
        self,
        user_id: int,
        jobs: list[TrendingJob],
        top_n: int,
    ) -> list[dict]:
        profile_text = UserUnderstandingService().get_user_profile_text(user_id)
        job_limit = min(len(jobs), 6 if active_provider() == "ollama" else 10)
        jobs_block = self._build_jobs_block(jobs, job_limit)

        prompt = (
            "You are a career matchmaker. Rank the trending jobs below by how well they suit this user.\n\n"
            f"USER PROFILE:\n{profile_text}\n\n"
            f"TRENDING JOBS:\n{jobs_block}\n\n"
            f"Return the TOP {top_n} jobs as a JSON object:\n"
            "{\n"
            '  "matches": [\n'
            "    {\n"
            '      "job_slug": "slug from the list above",\n'
            '      "fit_score": number from 0 to 100,\n'
            '      "fit_reason": "short reason this job fits",\n'
            '      "matched_skills": ["skills user already has"],\n'
            '      "missing_skills": ["skills to learn"]\n'
            "    }\n"
            "  ]\n"
            "}"
        )

        data = chat_json(
            prompt,
            max_output_tokens=2048 if active_provider() == "ollama" else 3072,
            provider=active_provider(),
        )
        if isinstance(data, dict):
            return data.get("matches") or []
        if isinstance(data, list):
            return data
        return []

    def _local_match_for_user(
        self,
        user_id: int,
        jobs: list[TrendingJob],
        top_n: int,
    ) -> list[JobMatch]:
        profile = Profile.objects.filter(user_id=user_id).first()
        rc = (profile.resume_context or {}) if profile else {}
        user_skills = {
            _normalize_token(skill)
            for skill in (rc.get("skills") or [])
            if skill
        }
        persona = rc.get("persona") or {}
        for interest in persona.get("interests") or []:
            user_skills.add(_normalize_token(str(interest)))

        predictions = list(
            CareerPrediction.objects.filter(user_id=user_id).select_related("career")
        )

        scored: list[tuple[float, TrendingJob, str, list[str], list[str]]] = []
        for job in jobs:
            score = 40.0
            reasons: list[str] = []
            matched: list[str] = []
            missing: list[str] = []

            for prediction in predictions:
                similarity = _text_similarity(prediction.career.name, job.title)
                if similarity >= 0.45:
                    score = max(score, prediction.confidence_pct * 0.85)
                    reasons.append(
                        f"Aligns with your {prediction.career.name} career prediction."
                    )

            job_skill_tokens = [_normalize_token(s) for s in (job.required_skills or []) if s]
            for skill_name, raw_skill in zip(job_skill_tokens, job.required_skills or []):
                if not skill_name:
                    continue
                if any(
                    skill_name in user_skill or user_skill in skill_name
                    for user_skill in user_skills
                    if user_skill
                ):
                    matched.append(raw_skill)
                    score += 4.0
                else:
                    missing.append(raw_skill)

            if matched:
                reasons.append(f"You already have relevant skills: {', '.join(matched[:3])}.")
            if missing:
                reasons.append(f"Consider learning: {', '.join(missing[:3])}.")
            if not reasons:
                reasons.append(
                    f"General fit based on your profile and demand for {job.title}."
                )

            scored.append((min(95.0, score), job, " ".join(reasons)[:300], matched[:5], missing[:5]))

        scored.sort(key=lambda row: row[0], reverse=True)
        JobMatch.objects.filter(user_id=user_id).delete()

        created: list[JobMatch] = []
        for fit_score, job, fit_reason, matched_skills, missing_skills in scored[:top_n]:
            match, _ = JobMatch.objects.update_or_create(
                user_id=user_id,
                job=job,
                defaults={
                    "fit_score": round(fit_score, 1),
                    "fit_reason": fit_reason,
                    "matched_skills": matched_skills,
                    "missing_skills": missing_skills,
                },
            )
            created.append(match)
        return created

    def _save_matches(self, user_id: int, jobs: list[TrendingJob], items: list[dict], top_n: int) -> list[JobMatch]:
        if not items:
            return []

        JobMatch.objects.filter(user_id=user_id).delete()
        slug_to_job = {job.slug: job for job in jobs}
        created: list[JobMatch] = []
        for item in items[:top_n]:
            if not isinstance(item, dict):
                continue
            slug = (item.get("job_slug") or "").strip().lower()
            job = slug_to_job.get(slug)
            if not job:
                title = (item.get("title") or "").strip().lower()
                for candidate in jobs:
                    if candidate.title.lower() == title:
                        job = candidate
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
                    "fit_reason": (item.get("fit_reason") or "")[:500],
                    "matched_skills": item.get("matched_skills") or [],
                    "missing_skills": item.get("missing_skills") or [],
                },
            )
            created.append(match)
        return created

    def match_for_user(self, user_id: int, top_n: int | None = None) -> list[JobMatch]:
        """Rank trending jobs for this user using the AI provider or local fallback."""
        top_n = top_n or self.default_match_count()
        jobs = list(TrendingJob.objects.all())
        if not jobs:
            jobs = self.refresh_trending()
        if not jobs:
            return []

        if _pipeline_using_ollama(user_id):
            logger.info(
                "Skipping trending LLM for user=%s — post-interview analysis is using Ollama.",
                user_id,
            )
            return self._local_match_for_user(user_id, jobs, top_n)

        try:
            items = self._fetch_user_matches(user_id, jobs, top_n)
            created = self._save_matches(user_id, jobs, items, top_n)
            if created:
                return created
        except LLMUnavailable as exc:
            if not _is_truncated_json_error(exc):
                logger.warning("Trending match LLM failed for user=%s: %s", user_id, exc)
            else:
                logger.warning(
                    "Trending match timed out or returned invalid JSON for user=%s; using local matching.",
                    user_id,
                )

        return self._local_match_for_user(user_id, jobs, top_n)
