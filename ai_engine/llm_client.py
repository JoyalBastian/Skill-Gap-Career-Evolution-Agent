"""Unified LLM entry point — routes to Gemini or Ollama based on AI_PROVIDER."""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import Any

import logging

from django.conf import settings

from ai_engine import gemini_client, ollama_client
from ai_engine.llm_common import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_TEMPERATURE,
    GenConfig,
    GeminiUnavailable,
    LLMUnavailable,
    cache_get,
    cache_set,
    extract_json,
    hash_prompt,
    user_message_for,
    flash_ai_error,
)

logger = logging.getLogger(__name__)

_OLLAMA_FALLBACK_MAX_TOKENS = 1536

__all__ = [
    "LLMUnavailable",
    "GeminiUnavailable",
    "chat_text",
    "chat_json",
    "is_enabled",
    "user_message_for",
    "flash_ai_error",
    "active_provider",
    "analysis_provider",
]


def active_provider() -> str:
    raw = (getattr(settings, "AI_PROVIDER", "gemini") or "gemini").strip().lower()
    if raw not in ("gemini", "ollama"):
        logger.warning("Unknown AI_PROVIDER=%r; using gemini.", raw)
        return "gemini"
    return raw


def analysis_provider() -> str:
    """Provider for post-interview analysis pipeline (may differ from AI_PROVIDER)."""
    raw = (getattr(settings, "ANALYSIS_AI_PROVIDER", "") or "").strip().lower()
    if not raw:
        return active_provider()
    if raw not in ("gemini", "ollama"):
        logger.warning("Unknown ANALYSIS_AI_PROVIDER=%r; using AI_PROVIDER.", raw)
        return active_provider()
    if raw == "gemini" and not gemini_client.gemini_is_configured():
        logger.warning(
            "ANALYSIS_AI_PROVIDER=gemini but GEMINI_API_KEY is not set; "
            "falling back to %s.",
            active_provider(),
        )
        return active_provider()
    return raw


def _ollama_fallback_enabled() -> bool:
    """Allow Gemini requests to fall back to local Ollama when cloud fails."""
    explicit = getattr(settings, "LLM_GEMINI_FALLBACK_OLLAMA", None)
    if explicit is not None:
        return bool(explicit)
    return active_provider() == "ollama"


def _resolve_model(provider: str | None = None) -> str:
    resolved = provider or active_provider()
    if resolved == "ollama":
        return ollama_client.model_name()
    return gemini_client.model_name()


def _gen_config(
    *,
    json_mode: bool,
    temperature: float | None,
    max_output_tokens: int | None,
    provider: str | None = None,
) -> GenConfig:
    resolved = provider or active_provider()
    if max_output_tokens is None:
        if resolved == "ollama":
            max_output_tokens = int(getattr(settings, "OLLAMA_DEFAULT_MAX_TOKENS", 768))
        else:
            max_output_tokens = DEFAULT_MAX_OUTPUT_TOKENS
    return GenConfig(
        temperature=temperature if temperature is not None else DEFAULT_TEMPERATURE,
        max_output_tokens=max_output_tokens,
        json_mode=json_mode,
    )


def _ollama_fallback_gen(gen: GenConfig) -> GenConfig:
    """Tighter token budget when falling back to local Ollama for speed."""
    cap = min(gen.max_output_tokens, _OLLAMA_FALLBACK_MAX_TOKENS)
    return replace(gen, max_output_tokens=cap)


def _dispatch_text(
    prompt: str,
    gen: GenConfig,
    provider: str | None = None,
    *,
    allow_fallback: bool = True,
) -> str:
    resolved = provider or active_provider()
    try:
        if resolved == "ollama":
            return ollama_client.ollama_chat_text(prompt, gen)
        return gemini_client.gemini_chat_text(prompt, gen)
    except LLMUnavailable as exc:
        if (
            not allow_fallback
            or resolved != "gemini"
            or not _ollama_fallback_enabled()
            or exc.provider != "gemini"
            or not ollama_client.ollama_is_reachable()
        ):
            raise
        logger.warning(
            "Gemini unavailable; falling back to Ollama for this request: %s",
            exc,
        )
        ollama_client.warmup_model()
        return ollama_client.ollama_chat_text(prompt, _ollama_fallback_gen(gen))


def chat_text(
    prompt: str,
    *,
    cache_key: str | None = None,
    ttl: timedelta | None = None,
    temperature: float | None = None,
    max_output_tokens: int | None = None,
    provider: str | None = None,
    allow_fallback: bool = True,
) -> str:
    resolved = provider or active_provider()
    gen = _gen_config(
        json_mode=False,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        provider=resolved,
    )
    model = _resolve_model(resolved)
    key_hash = hash_prompt(cache_key or prompt, resolved, model, gen)

    cached = cache_get(key_hash)
    if cached and cached.response_text:
        return cached.response_text

    text = _dispatch_text(prompt, gen, resolved, allow_fallback=allow_fallback)

    if ttl is not None:
        cache_set(key_hash, prompt, text, None, ttl, f"{resolved}:{model}")
    return text


def chat_json(
    prompt: str,
    *,
    cache_key: str | None = None,
    ttl: timedelta | None = None,
    temperature: float | None = None,
    max_output_tokens: int | None = None,
    provider: str | None = None,
    allow_fallback: bool = True,
) -> Any:
    resolved = provider or active_provider()
    gen = _gen_config(
        json_mode=True,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        provider=resolved,
    )
    model = _resolve_model(resolved)
    key_hash = hash_prompt(cache_key or prompt, resolved, model, gen)

    cached = cache_get(key_hash)
    if cached and cached.response_json:
        return cached.response_json

    raw = _dispatch_text(prompt, gen, resolved, allow_fallback=allow_fallback)
    data = extract_json(raw, provider=resolved)

    if ttl is not None:
        cache_set(key_hash, prompt, raw, data, ttl, f"{resolved}:{model}")
    return data


def is_enabled() -> bool:
    if active_provider() == "ollama":
        return ollama_client.ollama_is_reachable()
    return gemini_client.gemini_is_configured()
