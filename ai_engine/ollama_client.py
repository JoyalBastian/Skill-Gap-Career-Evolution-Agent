"""Ollama local LLM provider."""
from __future__ import annotations

import logging

import requests
from django.conf import settings

from ai_engine.llm_common import GenConfig, LLMUnavailable

logger = logging.getLogger(__name__)


def model_name() -> str:
    return getattr(settings, "OLLAMA_MODEL", "llama3.2")


def base_url() -> str:
    return (getattr(settings, "OLLAMA_BASE_URL", "http://localhost:11434") or "").rstrip("/")


def timeout_seconds() -> int:
    requested = int(getattr(settings, "OLLAMA_TIMEOUT", 120))
    gunicorn_timeout = int(getattr(settings, "GUNICORN_TIMEOUT", 30))
    # Keep provider timeout below worker timeout so request code can handle failures gracefully.
    upper_bound = max(5, gunicorn_timeout - 5)
    return max(5, min(requested, upper_bound))


def ollama_is_reachable() -> bool:
    try:
        r = requests.get(f"{base_url()}/api/tags", timeout=5)
        return r.status_code == 200
    except requests.RequestException:
        return False


def ollama_chat_text(prompt: str, gen: GenConfig) -> str:
    url = f"{base_url()}/api/chat"
    body: dict = {
        "model": model_name(),
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {
            "temperature": gen.temperature,
            "num_predict": gen.max_output_tokens,
        },
    }
    if gen.json_mode:
        body["format"] = "json"

    try:
        resp = requests.post(url, json=body, timeout=timeout_seconds())
    except requests.ConnectionError as e:
        url = base_url()
        hint = (
            "If using Docker: `docker compose --profile ollama up --build` "
            "(OLLAMA_BASE_URL must be http://ollama:11434 in the web container)."
        )
        if "localhost" in url or "127.0.0.1" in url:
            hint = (
                "Install Ollama from https://ollama.com, run `ollama serve`, "
                f"then `ollama pull {model_name()}`."
            )
        raise LLMUnavailable(
            f"Cannot connect to Ollama at {url}. {hint} ({e})",
            provider="ollama",
        ) from e
    except requests.Timeout as e:
        raise LLMUnavailable(
            f"Ollama request timed out after {timeout_seconds()}s. "
            "The model may still be loading — try again.",
            provider="ollama",
        ) from e
    except requests.RequestException as e:
        raise LLMUnavailable(f"Ollama request failed: {e}", provider="ollama") from e

    if resp.status_code != 200:
        raise LLMUnavailable(
            f"Ollama returned HTTP {resp.status_code}: {resp.text[:500]}",
            provider="ollama",
        )

    try:
        data = resp.json()
    except ValueError as e:
        raise LLMUnavailable("Ollama returned non-JSON response.", provider="ollama") from e

    message = data.get("message") or {}
    text = (message.get("content") or "").strip()
    if not text:
        raise LLMUnavailable("Ollama returned an empty response.", provider="ollama")
    return text
