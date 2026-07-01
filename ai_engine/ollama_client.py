"""Ollama local LLM provider."""
from __future__ import annotations

import logging
import time

import requests
from django.conf import settings

from ai_engine.llm_common import GenConfig, LLMUnavailable

logger = logging.getLogger(__name__)


def model_name() -> str:
    return getattr(settings, "OLLAMA_MODEL", "llama3.2:1b")


def base_url() -> str:
    return (getattr(settings, "OLLAMA_BASE_URL", "http://localhost:11434") or "").rstrip("/")


def keep_alive() -> str:
    return getattr(settings, "OLLAMA_KEEP_ALIVE", "30m") or "30m"


def num_ctx() -> int:
    return int(getattr(settings, "OLLAMA_NUM_CTX", 2048))


def max_retries() -> int:
    return int(getattr(settings, "OLLAMA_MAX_RETRIES", 2))


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


def _ollama_options(gen: GenConfig) -> dict:
    opts: dict = {
        "temperature": gen.temperature,
        "num_predict": gen.max_output_tokens,
    }
    ctx = num_ctx()
    if ctx > 0:
        opts["num_ctx"] = ctx
    return opts


def _connection_hint() -> str:
    url = base_url()
    if "localhost" in url or "127.0.0.1" in url:
        return (
            "Install Ollama from https://ollama.com, run `ollama serve`, "
            f"then `ollama pull {model_name()}`."
        )
    return (
        "If using Docker: `docker compose --profile ollama up --build` "
        "(OLLAMA_BASE_URL must be http://ollama:11434 in the web container)."
    )


def _parse_http_error(resp: requests.Response) -> LLMUnavailable:
    detail = resp.text[:500]
    try:
        payload = resp.json()
        detail = str(payload.get("error") or detail)
    except ValueError:
        pass

    lowered = detail.lower()
    if resp.status_code == 404 or "not found" in lowered:
        return LLMUnavailable(
            f"Ollama model '{model_name()}' is not installed. "
            f"Run: ollama pull {model_name()}",
            provider="ollama",
        )
    if resp.status_code in (502, 503, 504) or "loading" in lowered:
        return LLMUnavailable(
            f"Ollama is busy or still loading the model ({detail}).",
            provider="ollama",
            is_transient=True,
        )
    return LLMUnavailable(
        f"Ollama returned HTTP {resp.status_code}: {detail}",
        provider="ollama",
    )


def _should_retry(exc: LLMUnavailable, attempt: int, max_attempts: int) -> bool:
    if attempt >= max_attempts:
        return False
    if exc.is_transient:
        return True
    msg = str(exc).lower()
    if "timed out" in msg or "timeout" in msg:
        return False
    return any(
        token in msg
        for token in (
            "empty response",
            "still loading",
            "connection",
            "busy",
        )
    )


def _chat_request(prompt: str, gen: GenConfig) -> str:
    url = f"{base_url()}/api/chat"
    body: dict = {
        "model": model_name(),
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "keep_alive": keep_alive(),
        "options": _ollama_options(gen),
    }
    if gen.json_mode:
        body["format"] = "json"

    try:
        resp = requests.post(url, json=body, timeout=timeout_seconds())
    except requests.ConnectionError as e:
        raise LLMUnavailable(
            f"Cannot connect to Ollama at {base_url()}. {_connection_hint()} ({e})",
            provider="ollama",
        ) from e
    except requests.Timeout as e:
        raise LLMUnavailable(
            f"Ollama request timed out after {timeout_seconds()}s. "
            "The model may still be loading — try again.",
            provider="ollama",
            is_transient=False,
        ) from e
    except requests.RequestException as e:
        raise LLMUnavailable(f"Ollama request failed: {e}", provider="ollama") from e

    if resp.status_code != 200:
        raise _parse_http_error(resp)

    try:
        data = resp.json()
    except ValueError as e:
        raise LLMUnavailable("Ollama returned non-JSON response.", provider="ollama") from e

    if data.get("error"):
        raise LLMUnavailable(str(data["error"]), provider="ollama")

    message = data.get("message") or {}
    text = (message.get("content") or "").strip()
    if not text:
        done_reason = data.get("done_reason") or "unknown"
        raise LLMUnavailable(
            f"Ollama returned an empty response (done_reason={done_reason}).",
            provider="ollama",
            is_transient=done_reason in ("load", "unknown"),
        )

    done_reason = data.get("done_reason")
    if done_reason == "length" and gen.json_mode:
        logger.warning(
            "Ollama JSON response may be truncated (done_reason=length, num_predict=%s)",
            gen.max_output_tokens,
        )

    return text


def warmup_model() -> bool:
    """Load model into memory so the first user-facing call is faster."""
    if not ollama_is_reachable():
        return False
    url = f"{base_url()}/api/chat"
    body = {
        "model": model_name(),
        "messages": [{"role": "user", "content": "ping"}],
        "stream": False,
        "keep_alive": keep_alive(),
        "options": {"num_predict": 1, "num_ctx": min(num_ctx(), 512)},
    }
    try:
        resp = requests.post(url, json=body, timeout=min(timeout_seconds(), 90))
        ok = resp.status_code == 200
        if ok:
            logger.info("Ollama model %s warmed up", model_name())
        else:
            logger.warning("Ollama warmup HTTP %s: %s", resp.status_code, resp.text[:200])
        return ok
    except requests.RequestException as e:
        logger.warning("Ollama warmup failed: %s", e)
        return False


def ollama_chat_text(prompt: str, gen: GenConfig) -> str:
    attempts = max_retries() + 1
    last_exc: LLMUnavailable | None = None

    for attempt in range(1, attempts + 1):
        try:
            return _chat_request(prompt, gen)
        except LLMUnavailable as exc:
            last_exc = exc
            if _should_retry(exc, attempt, attempts):
                delay = min(8, 2 ** (attempt - 1))
                logger.warning(
                    "Ollama attempt %s/%s failed (%s); retrying in %ss",
                    attempt,
                    attempts,
                    exc,
                    delay,
                )
                time.sleep(delay)
                continue
            raise

    if last_exc is not None:
        raise last_exc
    raise LLMUnavailable("Ollama request failed.", provider="ollama")
