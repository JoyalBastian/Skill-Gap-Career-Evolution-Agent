"""Shared LLM utilities used by Gemini and Ollama providers."""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

_GEMINI_QUOTA_DOC_URL = "https://ai.google.dev/gemini-api/docs/rate-limits"

DEFAULT_TEMPERATURE = 0.4
DEFAULT_MAX_OUTPUT_TOKENS = 2048


@dataclass
class GenConfig:
    temperature: float = DEFAULT_TEMPERATURE
    top_p: float = 0.9
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    json_mode: bool = False


class LLMUnavailable(RuntimeError):
    """Raised when the configured LLM provider cannot fulfil a request."""

    def __init__(
        self,
        message: str,
        *,
        code: int | None = None,
        retry_after_seconds: float | None = None,
        is_quota: bool = False,
        is_transient: bool = False,
        provider: str = "llm",
    ):
        super().__init__(message)
        self.code = code
        self.retry_after_seconds = retry_after_seconds
        self.is_quota = is_quota
        self.is_transient = is_transient
        self.provider = provider

    @property
    def user_message(self) -> str:
        if self.provider == "ollama":
            msg = str(self).lower()
            if "timed out" in msg or "timeout" in msg:
                return (
                    "Analysis took too long to complete. "
                    "The service may still be starting — wait a minute and try again."
                )
            if "not installed" in msg or "not found" in msg:
                return (
                    "Analysis is not ready yet. "
                    "Please wait a moment and try again."
                )
            if "truncated" in msg or "not valid json" in msg:
                return (
                    "We received an incomplete response. "
                    "Please try again in a moment."
                )
            if "empty response" in msg:
                return (
                    "We did not receive a complete response. "
                    "Please wait and try again."
                )
            if self.is_transient or "busy" in msg or "still loading" in msg:
                return (
                    "The analysis service is busy or still starting. "
                    "Wait a few seconds and try again."
                )
            if "connect" in msg or "cannot connect" in msg:
                return (
                    "We could not reach the analysis service. "
                    "Please try again in a moment or contact support if this continues."
                )
            return (
                "Analysis is temporarily unavailable. "
                "Please try again in a moment."
            )
        if self.is_transient or self.code in (502, 503, 504):
            return (
                "The service is temporarily busy due to high demand. "
                "Please wait a few seconds and try again — your progress is saved."
            )
        if self.is_quota or self.code == 429:
            parts = [
                "Daily usage limit reached for career analysis.",
            ]
            if self.retry_after_seconds is not None and self.retry_after_seconds > 0:
                secs = int(self.retry_after_seconds) + 1
                if secs <= 120:
                    parts.append(f"Try again in about {secs} seconds.")
                else:
                    parts.append(
                        "The daily limit may be exhausted — please try again tomorrow."
                    )
            else:
                parts.append("Please try again later.")
            return " ".join(parts)
        return (
            "Analysis is temporarily unavailable. Please try again in a moment."
        )


# Backward-compatible alias
GeminiUnavailable = LLMUnavailable


def user_message_for(exc: BaseException) -> str:
    if isinstance(exc, LLMUnavailable):
        return exc.user_message
    if isinstance(exc, GeminiUnavailable):
        return exc.user_message
    return str(exc) or "Analysis is temporarily unavailable."


def hash_prompt(prompt: str, provider: str, model: str, gen: GenConfig) -> str:
    h = hashlib.sha256()
    h.update(provider.encode("utf-8"))
    h.update(b"::")
    h.update(model.encode("utf-8"))
    h.update(b"::")
    h.update(prompt.encode("utf-8"))
    h.update(f":t={gen.temperature}:j={gen.json_mode}".encode("utf-8"))
    return h.hexdigest()


def cache_get(key_hash: str):
    try:
        from apps.llm_cache.models import LLMCacheEntry
    except Exception:
        return None
    try:
        entry = LLMCacheEntry.objects.filter(key_hash=key_hash).first()
    except Exception:
        return None
    if not entry:
        return None
    if entry.expires_at and entry.expires_at < timezone.now():
        return None
    return entry


def cache_set(
    key_hash: str,
    prompt: str,
    response_text: str,
    response_json: Any,
    ttl: timedelta | None,
    model_name: str,
):
    try:
        from apps.llm_cache.models import LLMCacheEntry
    except Exception:
        return
    expires_at = timezone.now() + ttl if ttl else None
    try:
        LLMCacheEntry.objects.update_or_create(
            key_hash=key_hash,
            defaults={
                "prompt_preview": prompt[:500],
                "response_text": response_text or "",
                "response_json": response_json if isinstance(response_json, (dict, list)) else {},
                "model_name": model_name,
                "expires_at": expires_at,
            },
        )
    except Exception as e:
        logger.warning("LLM cache write failed: %s", e)


def strip_json_fences(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```\s*$", "", text)
    return text.strip()


def extract_json(raw: str, *, provider: str = "llm") -> Any:
    if not raw:
        raise LLMUnavailable("Empty response from AI model.", provider=provider)

    candidates = [raw.strip(), strip_json_fences(raw)]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    obj_match = re.search(r"\{.*\}", raw, re.DOTALL)
    arr_match = re.search(r"\[.*\]", raw, re.DOTALL)
    for c in (obj_match.group() if obj_match else None, arr_match.group() if arr_match else None):
        if not c:
            continue
        try:
            return json.loads(c)
        except json.JSONDecodeError:
            continue

    raise LLMUnavailable("AI response was not valid JSON.", provider=provider)
