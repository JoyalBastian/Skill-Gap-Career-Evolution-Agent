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
    api_key = (getattr(settings, "GEMINI_API_KEY", "") or "").strip()
    if not api_key:
        raise LLMUnavailable("GEMINI_API_KEY is not set.", provider="gemini")
    try:
        from google import genai
    except ImportError as e:
        raise LLMUnavailable(f"google-genai package not installed: {e}", provider="gemini") from e
    return genai.Client(api_key=api_key)


def _parse_api_error(exc: Exception) -> tuple[bool, bool, float | None, int | None]:
    text = str(exc)
    upper = text.upper()
    is_quota = (
        "429" in text
        or "RESOURCE_EXHAUSTED" in upper
        or "QUOTA" in upper
        or "RATE_LIMIT" in upper
    )
    is_transient = (
        "503" in text
        or "502" in text
        or "504" in text
        or "UNAVAILABLE" in upper
        or "HIGH DEMAND" in upper
        or "OVERLOADED" in upper
        or "DEADLINE EXCEEDED" in upper
    )
    code: int | None = None
    if is_quota:
        code = 429
    elif "503" in text or "UNAVAILABLE" in upper:
        code = 503
    elif "504" in text or "DEADLINE EXCEEDED" in upper:
        code = 504
    elif "502" in text:
        code = 502
    elif "400" in text or "INVALID_ARGUMENT" in upper or "API_KEY_INVALID" in upper:
        code = 400
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
    return is_quota, is_transient, retry_after, code


def _wrap_error(exc: Exception) -> LLMUnavailable:
    if isinstance(exc, LLMUnavailable):
        return exc
    is_quota, is_transient, retry_after, code = _parse_api_error(exc)
    return LLMUnavailable(
        str(exc),
        code=code,
        retry_after_seconds=retry_after,
        is_quota=is_quota,
        is_transient=is_transient,
        provider="gemini",
    )


def _retry_delay(attempt: int, retry_after: float | None) -> float:
    if retry_after and retry_after > 0:
        return min(retry_after + 0.5, 60)
    return min(2 ** attempt, 8)


def _max_retries() -> int:
    return max(1, int(getattr(settings, "GEMINI_MAX_RETRIES", 3)))


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


def _is_hard_quota_error(exc: Exception) -> bool:
    """True when free-tier quota is fully exhausted — retrying other models won't help."""
    text = str(exc)
    upper = text.upper()
    return "limit: 0" in text or (
        "RESOURCE_EXHAUSTED" in upper and "QUOTA" in upper and "FREE_TIER" in upper
    )


def _generate_with_fallback(client, prompt: str, gen: GenConfig) -> str:
    last_exc: Exception | None = None
    models = _models_to_try()
    primary = model_name()
    max_attempts = _max_retries()

    for model_idx, m in enumerate(models):
        for attempt in range(max_attempts):
            try:
                response = _generate_content(client, m, prompt, gen)
                text = (response.text or "").strip()
                if text and m != primary:
                    logger.info("Gemini succeeded with fallback model %s", m)
                return text
            except Exception as e:
                last_exc = e
                is_quota, is_transient, retry_after, _ = _parse_api_error(e)
                if is_quota and _is_hard_quota_error(e):
                    logger.warning(
                        "Gemini quota exhausted on %s; skipping further Gemini retries.",
                        m,
                    )
                    raise _wrap_error(e) from e
                retriable = is_quota or is_transient
                logger.warning(
                    "Gemini call failed (model=%s, attempt=%s/%s): %s",
                    m,
                    attempt + 1,
                    max_attempts,
                    e,
                )
                if retriable and attempt < max_attempts - 1:
                    delay = _retry_delay(attempt, retry_after)
                    logger.info("Retrying Gemini in %.1fs (model=%s)", delay, m)
                    time.sleep(delay)
                    continue
                if retriable and model_idx < len(models) - 1:
                    logger.info("Switching Gemini fallback model after failure on %s", m)
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
