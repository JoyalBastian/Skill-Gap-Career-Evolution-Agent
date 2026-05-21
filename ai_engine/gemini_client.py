"""Google Gemini provider implementation."""
from __future__ import annotations

import logging
import re
import time
from typing import Any

from django.conf import settings

from ai_engine.llm_common import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_TEMPERATURE,
    GenConfig,
    GeminiUnavailable,
    LLMUnavailable,
)

logger = logging.getLogger(__name__)

_MODEL_ALIASES = {
    "gemini-1.5-flash": "gemini-2.5-flash",
    "gemini-1.5-flash-latest": "gemini-2.5-flash",
    "gemini-1.5-flash-8b": "gemini-2.5-flash",
    "gemini-1.5-pro": "gemini-2.5-flash",
    "gemini-1.5-pro-latest": "gemini-2.5-flash",
    "gemini-pro": "gemini-2.5-flash",
}


def _resolve_model_name(raw: str) -> str:
    name = (raw or "").strip()
    if name.startswith("models/"):
        name = name[len("models/") :]
    return _MODEL_ALIASES.get(name, name or "gemini-2.5-flash")


def model_name() -> str:
    return _resolve_model_name(getattr(settings, "GEMINI_MODEL", "gemini-2.5-flash"))


def _fallback_models() -> list[str]:
    raw = getattr(settings, "GEMINI_FALLBACK_MODELS", "gemini-2.0-flash,gemini-flash-latest")
    if isinstance(raw, str):
        items = [m.strip() for m in raw.split(",") if m.strip()]
    else:
        items = list(raw)
    return [_resolve_model_name(m) for m in items]


def _models_to_try() -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for name in [model_name(), *_fallback_models()]:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def _client():
    api_key = getattr(settings, "GEMINI_API_KEY", "")
    if not api_key:
        raise LLMUnavailable("GEMINI_API_KEY is not set.", provider="gemini")
    try:
        from google import genai
    except ImportError as e:
        raise LLMUnavailable(f"google-genai package not installed: {e}", provider="gemini") from e
    return genai.Client(api_key=api_key)


def _parse_api_error(exc: Exception) -> tuple[bool, float | None, int | None]:
    text = str(exc)
    upper = text.upper()
    is_quota = (
        "429" in text
        or "RESOURCE_EXHAUSTED" in upper
        or "QUOTA" in upper
        or "RATE_LIMIT" in upper
    )
    code = 429 if is_quota else None
    retry_after: float | None = None
    for pattern in (
        r"retry in (\d+(?:\.\d+)?)\s*s",
        r'"retryDelay":\s*"(\d+)s"',
        r"retryDelay['\"]:\s*['\"](\d+)s",
    ):
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            retry_after = float(m.group(1))
            break
    return is_quota, retry_after, code


def _wrap_error(exc: Exception) -> LLMUnavailable:
    if isinstance(exc, LLMUnavailable):
        return exc
    is_quota, retry_after, code = _parse_api_error(exc)
    return LLMUnavailable(
        str(exc),
        code=code,
        retry_after_seconds=retry_after,
        is_quota=is_quota,
        provider="gemini",
    )


def _build_sdk_config(gen: GenConfig):
    try:
        from google.genai import types
    except ImportError:
        return None
    kwargs: dict[str, Any] = {
        "temperature": gen.temperature,
        "top_p": gen.top_p,
        "max_output_tokens": gen.max_output_tokens,
    }
    if gen.json_mode:
        kwargs["response_mime_type"] = "application/json"
    return types.GenerateContentConfig(**kwargs)


def _generate_content(client, model: str, prompt: str, gen: GenConfig):
    config = _build_sdk_config(gen)
    if config is not None:
        return client.models.generate_content(
            model=model,
            contents=prompt,
            config=config,
        )
    return client.models.generate_content(model=model, contents=prompt)


def _generate_with_fallback(client, prompt: str, gen: GenConfig) -> str:
    last_exc: Exception | None = None
    models = _models_to_try()
    primary = model_name()

    for m in models:
        for attempt in range(2):
            try:
                response = _generate_content(client, m, prompt, gen)
                text = (response.text or "").strip()
                if text and m != primary:
                    logger.info("Gemini succeeded with fallback model %s", m)
                return text
            except Exception as e:
                last_exc = e
                is_quota, retry_after, _ = _parse_api_error(e)
                logger.warning(
                    "Gemini call failed (model=%s, attempt=%s): %s",
                    m,
                    attempt + 1,
                    e,
                )
                if is_quota and retry_after and 0 < retry_after <= 90 and attempt == 0:
                    time.sleep(min(retry_after + 0.5, 90))
                    continue
                if is_quota and m != models[-1]:
                    break
                raise _wrap_error(e) from e

    raise _wrap_error(last_exc or RuntimeError("Gemini call failed"))


def gemini_chat_text(prompt: str, gen: GenConfig) -> str:
    client = _client()
    try:
        text = _generate_with_fallback(client, prompt, gen)
    except LLMUnavailable:
        raise
    except Exception as e:
        logger.error("Gemini text call failed: %s", e)
        raise _wrap_error(e) from e
    if not text:
        raise LLMUnavailable("Gemini returned an empty response.", provider="gemini")
    return text


def gemini_is_configured() -> bool:
    return bool(getattr(settings, "GEMINI_API_KEY", ""))
