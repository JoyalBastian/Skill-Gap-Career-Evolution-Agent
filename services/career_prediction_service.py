"""Gemini-only career prediction.

No sklearn, no embeddings, no curated career catalog. Gemini reads the user's
profile and returns the top career domains with confidence and reasoning.
CareerDomain rows are created on the fly so downstream services (gaps,
roadmap) can still reference stable IDs.
"""
from __future__ import annotations

import logging

from django.utils.text import slugify

from ai_engine.llm_client import GeminiUnavailable, chat_json
from apps.analytics.models import AIInsight
from apps.careers.models import CareerDomain, CareerPrediction
from services.dto import CareerPredictionDTO
from services.user_understanding_service import UserUnderstandingService

logger = logging.getLogger(__name__)

MODEL_VERSION = "gemini-v1"


def _ensure_career(name: str, slug: str | None, description: str, is_technical: bool) -> CareerDomain:
    s = slug or slugify(name)
    s = s[:50] or "career"
    career, created = CareerDomain.objects.get_or_create(
        slug=s,
        defaults={
            "name": name[:150],
            "description": description or name,
            "is_technical": bool(is_technical),
        },
    )
    if not created and description and not career.description:
        career.description = description
        career.save(update_fields=["description"])
    return career


def _normalize_confidences(items: list[dict]) -> list[dict]:
    """Scale confidence_pct values so they sum to ~100."""
    total = sum(float(i.get("confidence_pct") or 0) for i in items)
    if total <= 0:
        even = round(100 / len(items), 1) if items else 0
        for i in items:
            i["confidence_pct"] = even
        return items
    if 95 <= total <= 105:
        return items
    for i in items:
        i["confidence_pct"] = round((float(i.get("confidence_pct") or 0) / total) * 100, 1)
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
            "4. No duplicate or near-synonym careers (e.g. do not list both 'Data Scientist' and 'Data Science').\n"
            "5. confidence_pct values must be between 0 and 100 and should sum to roughly 100.\n"
            "6. Order by best fit first.\n"
            "7. Do NOT invent experience the user does not have.\n\n"
            f"USER PROFILE:\n{user_text}\n\n"
            f"Respond ONLY with a JSON object containing a 'predictions' array of {top_n} items.\n"
            "Each item must look like:\n"
            "{\n"
            "  \"career_name\": \"Human-readable career name\",\n"
            "  \"career_slug\": \"lowercase-hyphen-slug\",\n"
            "  \"description\": \"1-2 sentence description of the domain\",\n"
            "  \"is_technical\": true/false,\n"
            "  \"confidence_pct\": number from 0 to 100,\n"
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
            conf = float(item.get("confidence_pct") or 0)
            if conf < 0 or conf > 100:
                conf = max(0, min(100, conf))
            seen_slugs.add(slug)
            validated.append({**item, "career_name": name, "career_slug": slug, "confidence_pct": conf})

        if not validated:
            raise GeminiUnavailable("No valid career predictions after validation.")

        validated = _normalize_confidences(validated[:top_n])

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
                confidence_pct=round(confidence, 1),
                rank=rank,
                explanation_text=explanation,
                model_version=MODEL_VERSION,
            )
            dtos.append(CareerPredictionDTO(
                career_slug=career.slug,
                career_name=career.name,
                confidence_pct=round(confidence, 1),
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
