"""Quick Ollama smoke test — run inside web container."""
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))
os.chdir(BACKEND_ROOT)

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings.development")
django.setup()

from ai_engine.llm_client import chat_json, chat_text, is_enabled, active_provider
from ai_engine.ollama_client import model_name, base_url, warmup_model

print("provider:", active_provider())
print("model:", model_name())
print("url:", base_url())
print("reachable:", is_enabled())

if not is_enabled():
    sys.exit(1)

print("warmup:", warmup_model())

try:
    text = chat_text("Say hello in one word.", max_output_tokens=16)
    print("chat_text ok:", repr(text))
except Exception as e:
    print("chat_text error:", type(e).__name__, e)

try:
    data = chat_json(
        'Return JSON only: {"status": "ok", "value": 1}',
        max_output_tokens=128,
    )
    print("chat_json ok:", data)
except Exception as e:
    print("chat_json error:", type(e).__name__, e)
