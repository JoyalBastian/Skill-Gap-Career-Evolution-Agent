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
        provider: str = "llm",
    ):
        super().__init__(message)
        self.code = code
        self.retry_after_seconds = retry_after_seconds
        self.is_quota = is_quota
        self.provider = provider

    @property
    def user_message(self) -> str:
        if self.provider == "ollama":
            base = (
                getattr(settings, "OLLAMA_BASE_URL", "http://localhost:11434") or ""
            ).rstrip("/")
            model = getattr(settings, "OLLAMA_MODEL", "llama3.2")
            if "connect" in str(self).lower() or "not running" in str(self).lower():
                if "ollama:" in base or base.endswith("//ollama"):
                    return (
                        f"Cannot reach Ollama at {base}. "
                        "Start the stack with: docker compose --profile ollama up --build. "
                        f"Ensure the model is pulled: ollama pull {model}"
                    )
                return (
                    f"Cannot reach Ollama at {base}. "
                    "Install Ollama from https://ollama.com, keep it running, then in a terminal: "
                    f"ollama pull {model}"
                )
            return (
                "Local AI (Ollama) failed to respond. Check that Ollama is running and "
                f"the model '{model}' is installed (ollama pull {model})."
            )
        if self.is_quota or self.code == 429:
            parts = [
                "Gemini API quota limit reached for this project "
                "(free tier allows a small number of requests per day per model)."
            ]
            if self.retry_after_seconds is not None and self.retry_after_seconds > 0:
                secs = int(self.retry_after_seconds) + 1
                if secs <= 120:
                    parts.append(f"Try again in about {secs} seconds.")
                else:
                    parts.append(
                        "The daily limit may be exhausted — try again tomorrow, "
                        "switch AI_PROVIDER=ollama or GEMINI_MODEL in .env, or enable billing."
                    )
            else:
                parts.append(
                    "Try again later, use AI_PROVIDER=ollama, change GEMINI_MODEL, "
                    "or enable billing on Google AI Studio."
                )
            parts.append(f"Details: {_GEMINI_QUOTA_DOC_URL}")
            return " ".join(parts)
        return (
            "AI is temporarily unavailable. Please try again in a moment. "
            f"If this persists, check your provider settings and {_GEMINI_QUOTA_DOC_URL}"
        )


# Backward-compatible alias
GeminiUnavailable = LLMUnavailable


def user_message_for(exc: BaseException) -> str:
    if isinstance(exc, LLMUnavailable):
        return exc.user_message
    if isinstance(exc, GeminiUnavailable):
        return exc.user_message
    return str(exc) or "AI is temporarily unavailable."


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
