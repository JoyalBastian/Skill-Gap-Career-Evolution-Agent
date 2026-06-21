"""Gemini-only career prediction.

No sklearn, no embeddings, no curated career catalog. Gemini reads the user's
profile and returns the top career domains with confidence and reasoning.
CareerDomain rows are created on the fly so downstream services (gaps,
roadmap) can still reference stable IDs.
"""
from __future__ import annotations

import logging

from django.db import IntegrityError
from django.utils.text import slugify

from ai_engine.llm_client import GeminiUnavailable, chat_json
from apps.analytics.models import AIInsight
from apps.careers.models import CareerDomain, CareerPrediction
from services.dto import CareerPredictionDTO
from services.user_understanding_service import UserUnderstandingService

logger = logging.getLogger(__name__)

MODEL_VERSION = "gemini-v2-independent-fit"


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


class CareerPredictionService:
    def run_prediction(self, user_id: int, top_n: int = 5) -> list[CareerPredictionDTO]:
        user_text = UserUnderstandingService().get_user_profile_text(user_id)

        prompt = (
            "You are a career advisor. Given the user profile below, identify the TOP "
            f"{top_n} career domains that best fit them.\n\n"
            "STRICT RULES:\n"
            f"1. Return EXACTLY {top_n} items in the predictions array.\n"
            "2. career_name must be 2-4 words (human-readable job domain).\n"
            "3. explanation MUST cite at least one specific fact from the user profile (skill, role, answer, or goal).\n"
            "4. No duplicate or near-synonym careers (e.g. do not list both 'Data Scientist' and 'Data Science', or both 'AI Engineering' and 'Machine Learning').\n"
            "5. confidence_pct is an INDEPENDENT fit score from 0 to 100 for EACH career — scores do NOT need to sum to 100.\n"
            "   Use this rubric: 90–100 = excellent match with strong direct evidence in the profile; "
            "75–89 = strong fit; 60–74 = moderate fit; 40–59 = partial or stretch fit; below 40 = weak fit.\n"
            "   Every career MUST have a DIFFERENT confidence_pct — never assign the same score to two careers.\n"
            "   Differentiate clearly: the #1 career should score highest, with each next career lower than the previous.\n"
            "6. Do NOT invent experience the user does not have.\n\n"
            f"USER PROFILE:\n{user_text}\n\n"
            f"Respond ONLY with a JSON object containing a 'predictions' array of {top_n} items.\n"
            "Each item must look like:\n"
            "{\n"
            "  \"career_name\": \"Human-readable career name\",\n"
            "  \"career_slug\": \"lowercase-hyphen-slug\",\n"
            "  \"description\": \"1-2 sentence description of the domain\",\n"
            "  \"is_technical\": true/false,\n"
            "  \"confidence_pct\": independent fit score 0-100,\n"
            "  \"explanation\": \"why this career fits the user, citing profile facts\"\n"
            "}"
        )

        data = chat_json(prompt)
        items = []
        if isinstance(data, dict):
            items = data.get("predictions") or []
        elif isinstance(data, list):
            items = data

        if not items:
            raise GeminiUnavailable("Gemini returned no career predictions.")

        validated: list[dict] = []
        seen_slugs: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            name = (item.get("career_name") or "").strip()
            if not name:
                continue
            slug = (item.get("career_slug") or slugify(name)).strip().lower()[:50]
            if not slug or slug in seen_slugs:
                continue
            conf = _clip_fit_score(item.get("confidence_pct"))
            seen_slugs.add(slug)
            validated.append({**item, "career_name": name, "career_slug": slug, "confidence_pct": conf})

        if not validated:
            raise GeminiUnavailable("No valid career predictions after validation.")

        validated = _rank_by_fit_score(validated[:top_n])

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
                model_version=MODEL_VERSION,
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
            payload={"predictions": [p.__dict__ for p in dtos]},
        )
        return dtos

    def get_latest(self, user_id: int):
        return CareerPrediction.objects.filter(user_id=user_id).order_by("rank")
