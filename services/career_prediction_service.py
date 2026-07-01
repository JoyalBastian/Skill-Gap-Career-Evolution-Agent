"""Career prediction via configured analysis provider (Gemini or Ollama).

Gemini/Ollama reads the user's profile and returns the top career domains with
confidence and reasoning. CareerDomain rows are created on the fly so downstream
services (gaps, roadmap) can still reference stable IDs.
"""
from __future__ import annotations

import logging
import re

from django.db import IntegrityError
from django.utils.text import slugify

from ai_engine.llm_client import LLMUnavailable, analysis_provider, chat_json
from apps.analytics.models import AIInsight
from apps.careers.models import CareerDomain, CareerPrediction
from apps.users.models import Profile
from services.dto import CareerPredictionDTO
from services.user_understanding_service import UserUnderstandingService

logger = logging.getLogger(__name__)

MODEL_VERSION = "gemini-v2-independent-fit"
FALLBACK_MODEL_VERSION = "local-fallback-v1"

_TITLE_DOMAIN_MAP: list[tuple[tuple[str, ...], list[str]]] = [
    (
        ("full stack", "frontend", "backend", "web developer", "software engineer", "developer", "programmer"),
        ["Software Engineering", "Full Stack Development", "Cloud Engineering"],
    ),
    (
        ("data scientist", "data science", "machine learning", "ml engineer", "ai engineer"),
        ["Data Science", "Machine Learning Engineering", "AI Engineering"],
    ),
    (
        ("data analyst", "business analyst", "analytics"),
        ["Data Analytics", "Business Intelligence", "Data Engineering"],
    ),
    (
        ("devops", "sre", "platform engineer", "cloud engineer", "infrastructure"),
        ["DevOps Engineering", "Cloud Architecture", "Site Reliability Engineering"],
    ),
    (
        ("product manager", "product owner", "pm"),
        ["Product Management", "Technical Product Management", "Growth Product Management"],
    ),
    (
        ("designer", "ux", "ui", "product design"),
        ["UX Design", "Product Design", "Design Systems"],
    ),
    (
        ("marketing", "digital marketing", "content"),
        ["Digital Marketing", "Growth Marketing", "Brand Strategy"],
    ),
    (
        ("student", "intern", "graduate"),
        ["Software Engineering", "Data Analytics", "Product Management"],
    ),
]


def _normalize_compare(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _is_title_echo(career_name: str, current_title: str) -> bool:
    """True when a prediction merely repeats the user's current job title."""
    if not career_name or not current_title:
        return False
    name_norm = _normalize_compare(career_name)
    title_norm = _normalize_compare(current_title)
    if not name_norm or not title_norm:
        return False
    if name_norm == title_norm or name_norm in title_norm or title_norm in name_norm:
        return True
    stop = {"a", "an", "the", "and", "or", "senior", "junior", "lead", "staff", "ii", "iii"}
    name_words = {w for w in career_name.lower().split() if w not in stop}
    title_words = {w for w in current_title.lower().split() if w not in stop}
    return bool(name_words and title_words and name_words == title_words)


def _domains_from_title(current_title: str, is_technical: bool) -> list[str]:
    """Map a job title to forward-looking career domains (never the title itself)."""
    title_lower = (current_title or "").lower()
    for keywords, domains in _TITLE_DOMAIN_MAP:
        if any(keyword in title_lower for keyword in keywords):
            return domains
    if is_technical:
        return ["Software Engineering", "Technical Leadership", "Solutions Architecture"]
    return ["Business Management", "Operations Management", "Strategy Consulting"]


def _ensure_career(name: str, slug: str | None, description: str, is_technical: bool) -> CareerDomain:
    display_name = (name or "").strip()[:150]
    s = (slug or slugify(display_name)).strip().lower()[:50] or "career"
    desc = (description or display_name).strip()

    career = CareerDomain.objects.filter(slug=s).first()
    if not career:
        career = CareerDomain.objects.filter(name__iexact=display_name).first()

    if career:
        updated_fields: list[str] = []
        if desc and not career.description:
            career.description = desc
            updated_fields.append("description")
        if updated_fields:
            career.save(update_fields=updated_fields)
        return career

    try:
        return CareerDomain.objects.create(
            slug=s,
            name=display_name,
            description=desc,
            is_technical=bool(is_technical),
        )
    except IntegrityError:
        career = (
            CareerDomain.objects.filter(name__iexact=display_name).first()
            or CareerDomain.objects.filter(slug=s).first()
        )
        if career:
            return career
        raise


def _clip_fit_score(value) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        score = 0.0
    return round(max(0.0, min(100.0, score)), 1)


def _rank_by_fit_score(items: list[dict]) -> list[dict]:
    """Keep independent 0–100 fit scores; assign rank from highest score."""
    for item in items:
        item["confidence_pct"] = _clip_fit_score(item.get("confidence_pct"))
    ranked = sorted(items, key=lambda i: i["confidence_pct"], reverse=True)
    return _break_score_ties(ranked)


def _break_score_ties(items: list[dict]) -> list[dict]:
    """Ensure each rank has a strictly lower score than the one above it."""
    if not items:
        return items
    prev_score = items[0]["confidence_pct"]
    for index, item in enumerate(items):
        score = item["confidence_pct"]
        if index > 0 and score >= prev_score:
            score = _clip_fit_score(prev_score - 0.5)
        item["confidence_pct"] = score
        prev_score = score
    return items


def _normalize_career_name(raw: str) -> str:
    name = re.sub(r"\s+", " ", (raw or "").strip())
    if not name:
        return ""
    words = name.split()
    if len(words) > 4:
        name = " ".join(words[:4])
    return name.title()


class CareerPredictionService:
    def default_top_n(self) -> int:
        return 3 if analysis_provider() == "ollama" else 5

    def _ollama_max_tokens(self, top_n: int) -> int:
        return 4096 if top_n > 2 else 3072

    @staticmethod
    def _is_truncated_json_error(exc: BaseException) -> bool:
        msg = str(exc).lower()
        return "valid json" in msg or "truncated" in msg

    def _build_prompt(self, user_text: str, top_n: int) -> str:
        is_ollama = analysis_provider() == "ollama"
        explanation_rule = (
            "3. explanation max 80 characters; cite one profile fact.\n"
            if is_ollama
            else "3. explanation MUST cite at least one specific fact from the user profile (skill, role, answer, or goal).\n"
        )
        description_rule = (
            "7. description max 80 characters.\n"
            if is_ollama
            else ""
        )
        return (
            "You are a career advisor. Given the user profile below, identify the TOP "
            f"{top_n} career domains the user should pursue NEXT — not where they already are.\n\n"
            "STRICT RULES:\n"
            f"1. Return EXACTLY {top_n} items in the predictions array.\n"
            "2. career_name must be a 2-4 word CAREER DOMAIN or path "
            "(e.g. 'Data Science', 'Product Management', 'Cloud Engineering').\n"
            "3. NEVER return the user's exact current job title or role name as a prediction. "
            "Current role is background context only — predict forward-looking careers.\n"
            f"{explanation_rule}"
            "4. No duplicate or near-synonym careers.\n"
            "5. confidence_pct is an INDEPENDENT fit score from 0 to 100 for EACH career.\n"
            "   Every career MUST have a DIFFERENT confidence_pct.\n"
            "6. Weight career goals, interview answers, and aspirations above the current role.\n"
            "7. Do NOT invent experience the user does not have.\n"
            f"{description_rule}\n"
            f"USER PROFILE:\n{user_text}\n\n"
            f"Respond ONLY with a JSON object containing a 'predictions' array of {top_n} items.\n"
            "Each item must look like:\n"
            "{\n"
            "  \"career_name\": \"Human-readable career name\",\n"
            "  \"career_slug\": \"lowercase-hyphen-slug\",\n"
            "  \"description\": \"short description of the domain\",\n"
            "  \"is_technical\": true/false,\n"
            "  \"confidence_pct\": independent fit score 0-100,\n"
            "  \"explanation\": \"why this career fits the user\"\n"
            "}"
        )

    def _fetch_predictions(self, user_text: str, top_n: int, current_title: str = "") -> list[dict]:
        attempt_counts = [top_n]
        if analysis_provider() == "ollama" and top_n > 2:
            attempt_counts.append(2)

        data = None
        last_exc: BaseException | None = None
        resolved_top_n = top_n
        for attempt_n in attempt_counts:
            max_tokens = (
                self._ollama_max_tokens(attempt_n)
                if analysis_provider() == "ollama"
                else 3072
            )
            try:
                data = chat_json(
                    self._build_prompt(user_text, attempt_n),
                    max_output_tokens=max_tokens,
                    provider=analysis_provider(),
                )
                resolved_top_n = attempt_n
                break
            except LLMUnavailable as exc:
                last_exc = exc
                if not self._is_truncated_json_error(exc):
                    raise
                logger.warning(
                    "Career prediction JSON truncated (n=%s); retrying with fewer items.",
                    attempt_n,
                )

        if data is None:
            raise last_exc or LLMUnavailable(
                "Failed to generate career predictions.",
                provider=analysis_provider(),
            )

        items: list = []
        if isinstance(data, dict):
            items = data.get("predictions") or []
        elif isinstance(data, list):
            items = data

        if not items:
            raise LLMUnavailable(
                "AI model returned no career predictions.",
                provider=analysis_provider(),
            )

        validated: list[dict] = []
        seen_slugs: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            name = (item.get("career_name") or "").strip()
            if not name:
                continue
            if _is_title_echo(name, current_title):
                logger.info("Skipping title echo prediction: %s", name)
                continue
            slug = (item.get("career_slug") or slugify(name)).strip().lower()[:50]
            if not slug or slug in seen_slugs:
                continue
            conf = _clip_fit_score(item.get("confidence_pct"))
            seen_slugs.add(slug)
            validated.append({**item, "career_name": name, "career_slug": slug, "confidence_pct": conf})

        min_items = 1 if analysis_provider() == "ollama" else 2
        if len(validated) < min_items:
            raise LLMUnavailable(
                "No valid career predictions after validation.",
                provider=analysis_provider(),
            )

        return _rank_by_fit_score(validated[:resolved_top_n])

    def _save_predictions(
        self,
        user_id: int,
        validated: list[dict],
        *,
        model_version: str = MODEL_VERSION,
    ) -> list[CareerPredictionDTO]:
        CareerPrediction.objects.filter(user_id=user_id).delete()

        dtos: list[CareerPredictionDTO] = []
        for rank, item in enumerate(validated, start=1):
            career = _ensure_career(
                name=item["career_name"],
                slug=item["career_slug"],
                description=item.get("description") or "",
                is_technical=bool(item.get("is_technical", True)),
            )
            confidence = float(item["confidence_pct"])
            explanation = item.get("explanation") or ""

            CareerPrediction.objects.create(
                user_id=user_id,
                career=career,
                confidence_pct=confidence,
                rank=rank,
                explanation_text=explanation,
                model_version=model_version,
            )
            dtos.append(CareerPredictionDTO(
                career_slug=career.slug,
                career_name=career.name,
                confidence_pct=confidence,
                rank=rank,
                explanation=explanation,
            ))

        AIInsight.objects.create(
            user_id=user_id,
            insight_type="career_prediction",
            payload={"predictions": [p.__dict__ for p in dtos], "model_version": model_version},
        )
        return dtos

    def build_local_prediction_fallback(self, user_id: int) -> list[CareerPredictionDTO]:
        """Derive forward-looking career domains from goals and interview data when the LLM fails."""
        profile = Profile.objects.filter(user_id=user_id).first()
        rc = (profile.resume_context or {}) if profile else {}
        prep = rc.get("interview_prep") or {}
        persona = rc.get("persona") or {}
        is_technical = profile.is_technical_track if profile else True
        current_title = (prep.get("current_title") or rc.get("current_title") or "").strip()

        candidates: list[dict] = []
        seen: set[str] = set()

        def add_candidate(name: str, explanation: str, confidence: float) -> None:
            normalized = _normalize_career_name(name)
            if not normalized or _is_title_echo(normalized, current_title):
                return
            slug = slugify(normalized)[:50]
            if not slug or slug in seen:
                return
            seen.add(slug)
            candidates.append({
                "career_name": normalized,
                "career_slug": slug,
                "description": normalized,
                "is_technical": is_technical,
                "confidence_pct": confidence,
                "explanation": explanation[:200],
            })

        goals = (prep.get("career_goals") or (profile.bio if profile else "") or "").strip()
        if goals:
            goal_phrases = re.findall(
                r"(?:become|be|pursue|transition to|move into|work as|focus on|target)\s+"
                r"(?:a\s+)?([A-Za-z][A-Za-z\s/&-]{2,40})",
                goals,
                flags=re.IGNORECASE,
            )
            for phrase in goal_phrases[:3]:
                add_candidate(
                    phrase.strip(),
                    f"Matches your stated goal: {goals[:120]}.",
                    85.0 - len(candidates) * 4,
                )

        focus = (prep.get("focus_areas") or "").strip()
        if focus:
            for part in re.split(r"[,;/|]+", focus):
                part = part.strip()
                if len(part) > 3:
                    add_candidate(
                        part,
                        f"Aligned with your focus area: {part}.",
                        78.0 - len(candidates) * 3,
                    )

        for interest in (persona.get("interests") or [])[:3]:
            interest = str(interest).strip()
            if interest and not _is_title_echo(interest, current_title):
                add_candidate(
                    interest,
                    f"Supported by your interests and interview responses.",
                    72.0 - len(candidates) * 3,
                )

        uus = UserUnderstandingService()
        for qa_line in uus._get_recent_interview_qa(user_id, limit=4):
            answer = qa_line.split("\nA:", 1)[-1].strip() if "\nA:" in qa_line else ""
            for phrase in re.findall(
                r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\b",
                answer,
            )[:2]:
                if len(phrase) > 4 and phrase.lower() not in {"other", "none", "yes", "no"}:
                    add_candidate(
                        phrase,
                        "Inferred from your interview answers.",
                        68.0 - len(candidates) * 2,
                    )

        if current_title:
            for domain in _domains_from_title(current_title, is_technical):
                add_candidate(
                    domain,
                    f"Growth path from your background as {current_title}.",
                    70.0 - len(candidates) * 2,
                )

        if not candidates:
            default_name = "Software Engineering" if is_technical else "Business Management"
            add_candidate(
                default_name,
                "Suggested starting career path based on your profile.",
                65.0,
            )

        validated = _rank_by_fit_score(candidates[: self.default_top_n()])
        logger.info("Using local career prediction fallback for user %s", user_id)
        return self._save_predictions(user_id, validated, model_version=FALLBACK_MODEL_VERSION)

    def run_prediction(self, user_id: int, top_n: int | None = None) -> list[CareerPredictionDTO]:
        top_n = top_n or self.default_top_n()
        profile = Profile.objects.filter(user_id=user_id).first()
        rc = (profile.resume_context or {}) if profile else {}
        prep = rc.get("interview_prep") or {}
        current_title = (prep.get("current_title") or rc.get("current_title") or "").strip()
        user_text = UserUnderstandingService().get_user_profile_text(user_id)
        try:
            validated = self._fetch_predictions(user_text, top_n, current_title=current_title)
            return self._save_predictions(user_id, validated)
        except LLMUnavailable as exc:
            logger.warning("Career prediction LLM failed for user=%s: %s", user_id, exc)
            return self.build_local_prediction_fallback(user_id)

    def get_latest(self, user_id: int):
        return CareerPrediction.objects.filter(user_id=user_id).order_by("rank")
