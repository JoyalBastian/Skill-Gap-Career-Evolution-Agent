"""Generate ATS-friendly plain-text resumes tailored to a target job."""
from __future__ import annotations

import logging
import re

from ai_engine.llm_client import LLMUnavailable, active_provider, chat_json
from apps.jobs.models import ATSResume, TrendingJob
from apps.users.models import Profile
from services.user_understanding_service import UserUnderstandingService

logger = logging.getLogger(__name__)


def _clip_list(items, limit: int) -> list[str]:
    out: list[str] = []
    for item in items or []:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _profile_contact(profile: Profile | None, rc: dict) -> dict[str, str]:
    rc = rc or {}
    prep = rc.get("interview_prep") or {}
    return {
        "full_name": (prep.get("full_name") or rc.get("full_name") or "").strip(),
        "email": (rc.get("email") or "").strip(),
        "phone": (rc.get("phone") or "").strip(),
        "location": (rc.get("location") or "").strip(),
        "current_title": (prep.get("current_title") or rc.get("current_title") or "").strip(),
    }


def _format_ats_plain(data: dict, *, job_title: str) -> str:
    """Render structured resume data as ATS-safe plain text."""
    lines: list[str] = []

    name = (data.get("full_name") or "").strip()
    if name:
        lines.append(name.upper())
        lines.append("")

    contact_parts = []
    contact_line = (data.get("contact_line") or "").strip()
    if contact_line:
        contact_parts.append(contact_line)
    else:
        for key in ("email", "phone", "location", "linkedin"):
            val = (data.get(key) or "").strip()
            if val:
                contact_parts.append(val)
    if contact_parts:
        lines.append(" | ".join(contact_parts))
        lines.append("")

    headline = (data.get("headline") or job_title or "").strip()
    if headline:
        lines.append(headline)
        lines.append("")

    summary = (data.get("summary") or "").strip()
    if summary:
        lines.append("PROFESSIONAL SUMMARY")
        lines.append(summary)
        lines.append("")

    skills = _clip_list(data.get("skills") or [], 30)
    if skills:
        lines.append("CORE SKILLS")
        lines.append(", ".join(skills))
        lines.append("")

    experience = data.get("experience") or []
    if experience:
        lines.append("PROFESSIONAL EXPERIENCE")
        for role in experience[:6]:
            if not isinstance(role, dict):
                continue
            title = (role.get("title") or "").strip()
            company = (role.get("company") or "").strip()
            dates = (role.get("dates") or "").strip()
            header = " — ".join(p for p in (title, company) if p)
            if dates:
                header = f"{header} ({dates})" if header else dates
            if header:
                lines.append(header)
            for bullet in _clip_list(role.get("bullets") or [], 6):
                lines.append(f"- {bullet}")
            lines.append("")

    education = data.get("education") or []
    if education:
        lines.append("EDUCATION")
        for edu in education[:4]:
            if not isinstance(edu, dict):
                continue
            degree = (edu.get("degree") or "").strip()
            institution = (edu.get("institution") or "").strip()
            year = (edu.get("year") or "").strip()
            row = " — ".join(p for p in (degree, institution) if p)
            if year:
                row = f"{row} ({year})" if row else year
            if row:
                lines.append(row)
        lines.append("")

    certs = _clip_list(data.get("certifications") or [], 10)
    if certs:
        lines.append("CERTIFICATIONS")
        for cert in certs:
            lines.append(f"- {cert}")
        lines.append("")

    keywords = _clip_list(data.get("keywords_included") or [], 20)
    if keywords:
        lines.append("TARGET ROLE KEYWORDS")
        lines.append(", ".join(keywords))

    text = "\n".join(lines).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


class ATSResumeService:
    def _max_tokens(self) -> int:
        return 4096 if active_provider() == "ollama" else 3072

    def _build_prompt(
        self,
        *,
        user_id: int,
        job_title: str,
        company: str,
        job_description: str,
        required_skills: list[str],
    ) -> str:
        profile = Profile.objects.filter(user_id=user_id).first()
        rc = (profile.resume_context or {}) if profile else {}
        contact = _profile_contact(profile, rc)
        profile_text = UserUnderstandingService().get_user_profile_text(user_id)
        skills_line = ", ".join(required_skills[:15]) if required_skills else "(not specified)"

        return (
            "You are an expert resume writer specializing in ATS (Applicant Tracking System) optimization.\n"
            "Rewrite the candidate's resume for the target job below.\n\n"
            "STRICT ATS RULES:\n"
            "1. Output valid JSON only — we convert it to plain text.\n"
            "2. Use standard section names; no tables, columns, icons, or graphics.\n"
            "3. Naturally weave in keywords from the job title and required skills.\n"
            "4. Use concise bullet points with measurable outcomes where possible.\n"
            "5. Do NOT invent employers, degrees, or certifications the user does not have.\n"
            "6. If data is missing, omit that field rather than fabricating.\n"
            "7. Keep summary under 80 words; 3-5 bullets per recent role.\n\n"
            f"TARGET JOB TITLE: {job_title}\n"
            f"COMPANY: {company or 'Not specified'}\n"
            f"JOB DESCRIPTION:\n{job_description[:2500]}\n\n"
            f"REQUIRED / PREFERRED SKILLS: {skills_line}\n\n"
            f"CANDIDATE CONTACT (use when available):\n"
            f"- Name: {contact['full_name'] or 'unknown'}\n"
            f"- Email: {contact['email'] or 'unknown'}\n"
            f"- Phone: {contact['phone'] or 'unknown'}\n"
            f"- Location: {contact['location'] or 'unknown'}\n"
            f"- Current title: {contact['current_title'] or 'unknown'}\n\n"
            f"FULL CANDIDATE PROFILE:\n{profile_text}\n\n"
            "Respond ONLY with JSON:\n"
            "{\n"
            '  "full_name": "string",\n'
            '  "contact_line": "email | phone | location",\n'
            '  "headline": "targeted professional headline",\n'
            '  "summary": "ATS-optimized summary paragraph",\n'
            '  "skills": ["skill1", "skill2"],\n'
            '  "experience": [\n'
            '    {"title": "Role", "company": "Org", "dates": "YYYY - YYYY", "bullets": ["achievement"]}\n'
            "  ],\n"
            '  "education": [{"degree": "", "institution": "", "year": ""}],\n'
            '  "certifications": ["optional"],\n'
            '  "keywords_included": ["keywords from job description used in resume"]\n'
            "}"
        )

    def _save_resume(
        self,
        *,
        user_id: int,
        job_title: str,
        company: str,
        job_description: str,
        required_skills: list[str],
        content: str,
        keywords: list[str],
        trending_job: TrendingJob | None = None,
        source: str = "trending",
    ) -> ATSResume:
        return ATSResume.objects.create(
            user_id=user_id,
            trending_job=trending_job,
            source=source,
            job_title=job_title[:200],
            company=company[:150],
            job_description=job_description[:8000],
            required_skills=required_skills[:30],
            content=content,
            keywords_included=keywords[:30],
        )

    def generate_for_trending_job(self, user_id: int, job_id: int) -> ATSResume:
        job = TrendingJob.objects.get(id=job_id)
        description = "\n".join(
            p for p in (
                job.summary,
                job.growth_reason,
                f"Similar titles: {', '.join(job.suggested_titles or [])}",
            ) if p
        )
        skills = [str(s) for s in (job.required_skills or []) if str(s).strip()]
        return self._generate(
            user_id=user_id,
            job_title=job.title,
            company="",
            job_description=description,
            required_skills=skills,
            trending_job=job,
            source="trending",
        )

    def generate_for_vacancy(
        self,
        user_id: int,
        *,
        job_title: str,
        company: str,
        description: str,
        tags: list[str] | None = None,
    ) -> ATSResume:
        return self._generate(
            user_id=user_id,
            job_title=job_title,
            company=company,
            job_description=description,
            required_skills=tags or [],
            trending_job=None,
            source="vacancy",
        )

    def _generate(
        self,
        *,
        user_id: int,
        job_title: str,
        company: str,
        job_description: str,
        required_skills: list[str],
        trending_job: TrendingJob | None,
        source: str,
    ) -> ATSResume:
        profile = Profile.objects.filter(user_id=user_id).first()
        profile_text = UserUnderstandingService().get_user_profile_text(user_id)
        if profile_text == "User has not yet provided profile data.":
            raise LLMUnavailable(
                "Upload a resume or complete the career interview before generating a tailored resume.",
                provider="unknown",
            )

        prompt = self._build_prompt(
            user_id=user_id,
            job_title=job_title,
            company=company,
            job_description=job_description,
            required_skills=required_skills,
        )
        data = chat_json(prompt, max_output_tokens=self._max_tokens())
        if not isinstance(data, dict):
            raise LLMUnavailable("Resume generation did not return valid JSON.", provider="unknown")

        content = _format_ats_plain(data, job_title=job_title)
        if not content or len(content) < 80:
            raise LLMUnavailable("Generated resume was too short. Please try again.", provider="unknown")

        keywords = _clip_list(data.get("keywords_included") or [], 30)
        return self._save_resume(
            user_id=user_id,
            job_title=job_title,
            company=company,
            job_description=job_description,
            required_skills=required_skills,
            content=content,
            keywords=keywords,
            trending_job=trending_job,
            source=source,
        )

    def get_latest_for_job(self, user_id: int, job_id: int) -> ATSResume | None:
        return (
            ATSResume.objects.filter(user_id=user_id, trending_job_id=job_id)
            .order_by("-created_at")
            .first()
        )
