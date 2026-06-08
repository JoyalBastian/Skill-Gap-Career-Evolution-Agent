"""Fetch live job vacancies from free public job-board APIs."""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone as dt_timezone

import requests
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

REMOTIVE_URL = "https://remotive.com/api/remote-jobs"
ARBEITNOW_URL = "https://www.arbeitnow.com/api/job-board-api"


@dataclass
class Vacancy:
    title: str
    company: str
    company_logo: str
    location: str
    is_remote: bool
    url: str
    posted_date: datetime | None
    salary: str
    tags: list[str] = field(default_factory=list)
    job_type: str = ""
    source: str = ""


@dataclass
class VacancySearchResult:
    vacancies: list[Vacancy]
    error: str | None = None


class JobVacancyService:
    def _timeout(self) -> int:
        return int(getattr(settings, "JOB_API_TIMEOUT", 10))

    def _cache_ttl(self) -> int:
        minutes = int(getattr(settings, "JOB_VACANCY_CACHE_MINUTES", 30))
        return max(60, minutes * 60)

    def _cache_key(self, keyword: str, location: str, remote_only: bool, limit: int) -> str:
        raw = f"{keyword}|{location}|{remote_only}|{limit}"
        digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
        return f"job_vacancies::{digest}"

    def search_vacancies(
        self,
        *,
        keyword: str = "",
        location: str = "",
        remote_only: bool = True,
        limit: int = 30,
    ) -> VacancySearchResult:
        keyword = (keyword or "").strip()
        location = (location or "").strip()
        limit = max(1, min(int(limit), 50))

        key = self._cache_key(keyword, location, remote_only, limit)
        cached = cache.get(key)
        if cached is not None:
            return cached

        provider = (getattr(settings, "JOB_API_PROVIDER", "remotive") or "remotive").lower()
        errors: list[str] = []
        vacancies: list[Vacancy] = []

        if provider in ("remotive", "both"):
            try:
                vacancies.extend(self._fetch_remotive(keyword, limit))
            except requests.RequestException as exc:
                logger.warning("Remotive API failed: %s", exc)
                errors.append("Remotive job listings are temporarily unavailable.")

        use_arbeitnow = (
            provider in ("arbeitnow", "both")
            or bool(location)
            or not vacancies
        )
        if use_arbeitnow:
            try:
                vacancies.extend(self._fetch_arbeitnow(keyword, limit * 2))
            except requests.RequestException as exc:
                logger.warning("Arbeitnow API failed: %s", exc)
                errors.append("Arbeitnow job listings are temporarily unavailable.")

        vacancies = self._dedupe(vacancies)
        vacancies = self._filter_vacancies(vacancies, location=location, remote_only=remote_only)
        vacancies.sort(key=lambda v: v.posted_date or datetime.min.replace(tzinfo=dt_timezone.utc), reverse=True)
        vacancies = vacancies[:limit]

        error = None
        if not vacancies and errors:
            error = " ".join(errors) + " Please try again in a few minutes."
        elif not vacancies:
            error = None

        result = VacancySearchResult(vacancies=vacancies, error=error)
        if vacancies or not errors:
            cache.set(key, result, self._cache_ttl())
        return result

    def _fetch_remotive(self, keyword: str, limit: int) -> list[Vacancy]:
        params: dict = {"limit": limit}
        if keyword:
            params["search"] = keyword

        resp = requests.get(REMOTIVE_URL, params=params, timeout=self._timeout())
        resp.raise_for_status()
        payload = resp.json()
        jobs = payload.get("jobs") or []
        out: list[Vacancy] = []

        for item in jobs:
            if not isinstance(item, dict):
                continue
            title = (item.get("title") or "").strip()
            url = (item.get("url") or "").strip()
            if not title or not url:
                continue
            loc = (item.get("candidate_required_location") or "Remote").strip()
            posted = self._parse_date(item.get("publication_date"))
            tags = [str(t) for t in (item.get("tags") or []) if t][:8]
            out.append(
                Vacancy(
                    title=title[:200],
                    company=(item.get("company_name") or "Unknown").strip()[:120],
                    company_logo=(item.get("company_logo") or "").strip(),
                    location=loc[:120],
                    is_remote=True,
                    url=url,
                    posted_date=posted,
                    salary=(item.get("salary") or "").strip()[:120],
                    tags=tags,
                    job_type=(item.get("job_type") or "").replace("_", " ").strip()[:40],
                    source="remotive",
                )
            )
        return out

    def _fetch_arbeitnow(self, keyword: str, limit: int) -> list[Vacancy]:
        resp = requests.get(ARBEITNOW_URL, timeout=self._timeout())
        resp.raise_for_status()
        payload = resp.json()
        jobs = payload.get("data") or []
        kw = keyword.lower()
        out: list[Vacancy] = []

        for item in jobs:
            if not isinstance(item, dict):
                continue
            title = (item.get("title") or "").strip()
            url = (item.get("url") or "").strip()
            if not title or not url:
                continue
            if kw and kw not in title.lower() and kw not in (item.get("description") or "").lower():
                company = (item.get("company_name") or "").lower()
                tags_text = " ".join(str(t) for t in (item.get("tags") or [])).lower()
                if kw not in company and kw not in tags_text:
                    continue

            created = item.get("created_at")
            posted = None
            if created:
                try:
                    posted = datetime.fromtimestamp(int(created), tz=dt_timezone.utc)
                except (TypeError, ValueError, OSError):
                    posted = None

            tags = [str(t) for t in (item.get("tags") or []) if t][:8]
            job_types = item.get("job_types") or []
            job_type = ", ".join(str(j) for j in job_types[:2]) if job_types else ""

            out.append(
                Vacancy(
                    title=title[:200],
                    company=(item.get("company_name") or "Unknown").strip()[:120],
                    company_logo="",
                    location=(item.get("location") or "Unknown").strip()[:120],
                    is_remote=bool(item.get("remote")),
                    url=url,
                    posted_date=posted,
                    salary="",
                    tags=tags,
                    job_type=job_type[:40],
                    source="arbeitnow",
                )
            )
            if len(out) >= limit:
                break
        return out

    def _parse_date(self, value) -> datetime | None:
        if not value:
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo else timezone.make_aware(value)
        text = str(value).strip()
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
            try:
                dt = datetime.strptime(text[:19], fmt)
                return timezone.make_aware(dt)
            except ValueError:
                continue
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return dt if dt.tzinfo else timezone.make_aware(dt)
        except ValueError:
            return None

    def _dedupe(self, vacancies: list[Vacancy]) -> list[Vacancy]:
        seen: set[str] = set()
        out: list[Vacancy] = []
        for v in vacancies:
            key = v.url.lower().strip()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(v)
        return out

    def _filter_vacancies(
        self,
        vacancies: list[Vacancy],
        *,
        location: str,
        remote_only: bool,
    ) -> list[Vacancy]:
        loc = location.lower().strip()
        out: list[Vacancy] = []
        for v in vacancies:
            if remote_only and not v.is_remote:
                continue
            if loc:
                haystack = f"{v.location} {v.title} {v.company}".lower()
                if loc not in haystack:
                    continue
            out.append(v)
        return out
