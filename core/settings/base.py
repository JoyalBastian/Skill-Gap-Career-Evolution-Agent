"""
Base Django settings for SkillGap AI.

This is a Gemini-only application: there is no sklearn, no SBERT, no spaCy,
and no seeded knowledge catalog. The only external AI dependency is Google
Gemini (via the `google-genai` package).
"""
import os
from pathlib import Path

import environ

# backend/ (directory containing manage.py)
BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    GEMINI_ENABLED=(bool, False),
    RUN_AI_PIPELINE_ON_RESUME=(bool, False),
    OLLAMA_TIMEOUT=(int, 120),
)

# Load environment from backend/.env (same folder as manage.py)
env_file = BASE_DIR / ".env"
if env_file.exists():
    environ.Env.read_env(str(env_file))
else:
    # Legacy location: skillgap_ai/.env (parent of backend/)
    legacy_env = BASE_DIR.parent / ".env"
    if legacy_env.exists():
        environ.Env.read_env(str(legacy_env))

SECRET_KEY = env("SECRET_KEY", default="django-insecure-dev-key-change-in-production")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "apps.authentication",
    "apps.users",
    "apps.questionnaire",
    "apps.careers",
    "apps.skills",
    "apps.recommendations",
    "apps.roadmap",
    "apps.analytics",
    "apps.progress",
    "apps.jobs",
    "apps.llm_cache",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "core.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.site_settings",
                "apps.users.context_processors.journey",
            ],
        },
    },
]

WSGI_APPLICATION = "core.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / env("DATABASE_PATH", default="db.sqlite3"),
        "OPTIONS": {
            # Seconds to wait for a lock (pairs with PRAGMA busy_timeout in core/sqlite.py)
            "timeout": 60,
        },
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATICFILES_STORAGE = "whitenoise.storage.CompressedStaticFilesStorage"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "static" / "uploads"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "authentication:login"
LOGIN_REDIRECT_URL = "users:dashboard"
LOGOUT_REDIRECT_URL = "authentication:login"

REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
}

# ---------------------------------------------------------------------------
# AI provider: gemini (cloud) or ollama (local Docker / ollama serve)
# ---------------------------------------------------------------------------
AI_PROVIDER = env("AI_PROVIDER", default="gemini")  # "gemini" | "ollama"

GEMINI_API_KEY = env("GEMINI_API_KEY", default="")
GEMINI_MODEL = env("GEMINI_MODEL", default="gemini-2.5-flash")
GEMINI_FALLBACK_MODELS = env(
    "GEMINI_FALLBACK_MODELS",
    default="gemini-2.0-flash,gemini-flash-latest",
)
RUN_AI_PIPELINE_ON_RESUME = env("RUN_AI_PIPELINE_ON_RESUME", default=False)
GEMINI_ENABLED = env("GEMINI_ENABLED")

OLLAMA_BASE_URL = env("OLLAMA_BASE_URL", default="http://localhost:11434")
OLLAMA_MODEL = env("OLLAMA_MODEL", default="llama3.2")
OLLAMA_TIMEOUT = env("OLLAMA_TIMEOUT")

FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
