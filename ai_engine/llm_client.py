"""Unified LLM entry point — routes to Gemini or Ollama based on AI_PROVIDER."""
from __future__ import annotations

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
)

logger = logging.getLogger(__name__)

__all__ = [
    "LLMUnavailable",
    "GeminiUnavailable",
    "chat_text",
    "chat_json",
    "is_enabled",
    "user_message_for",
    "active_provider",
]


def active_provider() -> str:
    raw = (getattr(settings, "AI_PROVIDER", "gemini") or "gemini").strip().lower()
    if raw not in ("gemini", "ollama"):
        logger.warning("Unknown AI_PROVIDER=%r; using gemini.", raw)
        return "gemini"
    return raw


def _resolve_model() -> str:
    if active_provider() == "ollama":
        return ollama_client.model_name()
    return gemini_client.model_name()


def _gen_config(
    *,
    json_mode: bool,
    temperature: float | None,
    max_output_tokens: int | None,
) -> GenConfig:
    return GenConfig(
        temperature=temperature if temperature is not None else DEFAULT_TEMPERATURE,
        max_output_tokens=max_output_tokens or DEFAULT_MAX_OUTPUT_TOKENS,
        json_mode=json_mode,
    )


def _dispatch_text(prompt: str, gen: GenConfig) -> str:
    if active_provider() == "ollama":
        return ollama_client.ollama_chat_text(prompt, gen)
    return gemini_client.gemini_chat_text(prompt, gen)


def chat_text(
    prompt: str,
    *,
    cache_key: str | None = None,
    ttl: timedelta | None = None,
    temperature: float | None = None,
    max_output_tokens: int | None = None,
) -> str:
    gen = _gen_config(json_mode=False, temperature=temperature, max_output_tokens=max_output_tokens)
    provider = active_provider()
    model = _resolve_model()
    key_hash = hash_prompt(cache_key or prompt, provider, model, gen)

    cached = cache_get(key_hash)
    if cached and cached.response_text:
        return cached.response_text

    text = _dispatch_text(prompt, gen)

    if ttl is not None:
        cache_set(key_hash, prompt, text, None, ttl, f"{provider}:{model}")
    return text


def chat_json(
    prompt: str,
    *,
    cache_key: str | None = None,
    ttl: timedelta | None = None,
    temperature: float | None = None,
    max_output_tokens: int | None = None,
) -> Any:
    gen = _gen_config(json_mode=True, temperature=temperature, max_output_tokens=max_output_tokens)
    provider = active_provider()
    model = _resolve_model()
    key_hash = hash_prompt(cache_key or prompt, provider, model, gen)

    cached = cache_get(key_hash)
    if cached and cached.response_json:
        return cached.response_json

    raw = _dispatch_text(prompt, gen)
    data = extract_json(raw, provider=provider)

    if ttl is not None:
        cache_set(key_hash, prompt, raw, data, ttl, f"{provider}:{model}")
    return data


def is_enabled() -> bool:
    if active_provider() == "ollama":
        return ollama_client.ollama_is_reachable()
    return gemini_client.gemini_is_configured()
